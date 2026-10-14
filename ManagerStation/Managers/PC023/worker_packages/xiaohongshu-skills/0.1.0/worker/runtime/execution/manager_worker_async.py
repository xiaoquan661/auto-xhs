"""Detached employee runner for one accepted manager task."""

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
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

try:
    from .direct_creator_metrics import DirectCreatorMetricsRunner, is_direct_creator_task
    from .employee_codex_runner import EmployeeCodexRunner
    from .manager_worker_service import ManagerWorkerService
    from .run_lock import RunLock
except ImportError:
    from direct_creator_metrics import DirectCreatorMetricsRunner, is_direct_creator_task
    from employee_codex_runner import EmployeeCodexRunner
    from manager_worker_service import ManagerWorkerService
    from run_lock import RunLock


def queue_ready(state: dict, execution: dict) -> bool:
    """Keep acceptance order inside one account, without holding other accounts."""
    from worker_batch_scheduler import queue_status

    return queue_status(state, execution)[0] == "READY"


def task_stop_checker(
    service: ManagerWorkerService, task_id: str, remote_execution_id: str
) -> Callable[[], bool]:
    """Read the large worker state at most once per 30 seconds during an Agent turn."""
    last_status_check = float("-inf")
    stop_requested = False

    def should_stop() -> bool:
        nonlocal last_status_check, stop_requested
        now = time.monotonic()
        if now - last_status_check < 30:
            return stop_requested
        last_status_check = now
        execution = service.task_status(task_id, remote_execution_id=remote_execution_id)[
            "execution"
        ]
        stop_requested = execution["state"] != "RUNNING"
        return stop_requested

    return should_stop


def run(state_root: str, task_id: str, remote_execution_id: str) -> int:
    service = ManagerWorkerService(root=state_root)
    execution = service._store.snapshot()["executions"][remote_execution_id]
    slot = str(execution["task"].get("account_slot") or "default")
    lock = RunLock(str(Path(service.root) / "agent-locks" / (quote(slot, safe="") + ".lock")))
    while True:
        state = service._store.snapshot()
        execution = state["executions"][remote_execution_id]
        if state["machine"]["paused"] or execution["state"] != "ACCEPTED":
            return 0
        if queue_ready(state, execution) and lock.acquire(timeout=1):
            break
        time.sleep(2)
    try:
        direct_creator = is_direct_creator_task(execution["task"])
        service.send_task_progress(
            task_id,
            remote_execution_id=remote_execution_id,
            stage="starting",
            summary="本机 CLI 开始采集作品数据" if direct_creator else "员工Agent开始执行任务",
        )

        def claimed(receipt: dict) -> None:
            service.send_task_progress(
                task_id,
                remote_execution_id=remote_execution_id,
                stage="agent_claimed",
                summary="员工 Codex Agent 已读取并领取任务",
                details=receipt,
            )

        should_stop = task_stop_checker(service, task_id, remote_execution_id)

        service.run_task(
            task_id,
            remote_execution_id=remote_execution_id,
            executor=(
                (lambda task: {"state": "BLOCKED", "message": "前置任务未成功完成，请核对前置回执"})
                if any(
                    row["state"] not in {"SUCCESS", "PARTIAL_SUCCESS"}
                    for row in state["executions"].values()
                    if row["task_id"]
                    in (execution["task"].get("parameters") or {}).get("depends_on_task_ids", [])
                )
                else (
                    DirectCreatorMetricsRunner(
                        service.root, worker_snapshot=service._store.snapshot
                    )
                    if direct_creator
                    else EmployeeCodexRunner(
                        service.root,
                        on_claim=claimed,
                        should_stop=should_stop,
                        on_bug=lambda payload: service.debug_action("report", payload),
                    )
                )
            ),
        )
        service.record_outcome_progress(task_id, remote_execution_id=remote_execution_id)
        service.push_task_result(task_id, remote_execution_id=remote_execution_id)
        return 0
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行一条员工异步任务")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--remote-execution-id", required=True)
    parser.add_argument("--startup-receipt")
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    if arguments.startup_receipt:
        receipt = Path(arguments.startup_receipt)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(receipt)
    raise SystemExit(run(arguments.state_root, arguments.task_id, arguments.remote_execution_id))
