"""Run one employee-local scheduled reply check through the worker inbox/outbox."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import argparse
import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .manager_worker_service import ManagerWorkerService
except ImportError:
    from manager_worker_service import ManagerWorkerService


def run_check(
    config: dict[str, Any], worker: ManagerWorkerService, *, now: datetime | None = None
) -> dict[str, Any]:
    """Queue a bounded reply task, recording a deferred check when the account is busy."""
    if not config.get("enabled", False):
        return {"status": "DISABLED"}
    now = now or datetime.now(UTC)
    minutes = int(config.get("interval_minutes", 20))
    bucket = datetime.fromtimestamp(int(now.timestamp()) // (minutes * 60) * minutes * 60, UTC)
    task = copy.deepcopy(config["task"])
    task_id = f"{task['machine_id'].lower()}-{task['account_slot']}-replies-{bucket:%Y%m%d-%H%M}"
    snapshot = worker.dashboard_snapshot()
    executions = snapshot["executions"]
    if any(item["task_id"] == task_id for item in executions):
        return {"status": "ALREADY_CHECKED", "task_id": task_id}
    busy = any(
        item.get("task", {}).get("account_slot") == task["account_slot"]
        and item.get("state")
        in {
            "ACCEPTED",
            "RUNNING",
            "PAUSED",
            "PAUSE_REQUESTED",
            "RECONCILIATION_REQUIRED",
            "RESULT_UNKNOWN",
        }
        for item in executions
    )
    task.update(
        task_id=task_id,
        source_type="executor",
        result_transport={
            "type": "sftp_outbox",
            "collector": "manager_pull",
        },
    )
    task["parameters"]["interval_minutes"] = minutes
    envelope = {
        "task_id": task_id,
        "dispatch_id": task_id,
        "target_machine_id": task["machine_id"],
        "allowed_versions": task["allowed_versions"],
        "task": task,
    }
    if busy:
        worker.accept_task(envelope)
        worker.run_task(
            task_id,
            executor=lambda request: {
                "state": "SUCCESS",
                "message": "账号任务串行执行，本轮自查顺延",
                "business_result": {
                    "outcome": "DEFERRED_ACCOUNT_BUSY",
                    "checked_at": now.isoformat(),
                    "inbox_checked": False,
                    "next_check_after_minutes": minutes,
                "account_slot": task["account_slot"],
                "platform": task["platform"],
                "capability": "reply-inbox",
                },
                "item_results": [],
            },
        )
        worker.push_task_result(task_id)
        return {"status": "DEFERRED_ACCOUNT_BUSY", "task_id": task_id}
    inbox = Path(snapshot["exchange"]["inbox_root"]) / "pending"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / f"{task_id}.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return worker.wake_task(task_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="员工机定时评论与私信自查")
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    result = run_check(config, ManagerWorkerService())
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
