"""Run creator metrics through the local CLI inside the employee task lifecycle."""

from __future__ import annotations

import json
import subprocess
import sys
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

try:
    from .daily_creator_collection import local_published_note_ids
except ImportError:
    from daily_creator_collection import local_published_note_ids

if TYPE_CHECKING:
    from manager_worker_service import ManagerWorkerService


def is_direct_creator_task(task: dict[str, Any]) -> bool:
    parameters = task.get("parameters") or {}
    return (
        task.get("capability") == "collect-operations-metrics"
        and parameters.get("source") == "creator_manager"
    )


class DirectCreatorMetricsRunner:
    def __init__(
        self,
        state_root: str | Path,
        *,
        worker_snapshot: Callable[[], dict[str, Any]],
        command_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.root = Path(state_root)
        self.worker_snapshot = worker_snapshot
        self.command_runner = command_runner

    def __call__(self, task: dict[str, Any]) -> dict[str, Any]:
        slot = str(task["account_slot"])
        parameters = task.get("parameters") or {}
        config_path = self.root / "daily-creator" / "config.json"
        config = (
            json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
        )
        slot_config = (config.get("accounts") or {}).get(slot) or {}
        local_only = bool(
            parameters.get("local_published_only") or slot_config.get("local_published_only")
        )
        note_ids = list(parameters.get("note_ids") or [])
        if local_only:
            state = self.worker_snapshot()
            note_ids = local_published_note_ids(list(state.get("executions", {}).values()), slot)
            if not note_ids:
                return {
                    "state": "SUCCESS",
                    "message": "本槽位尚无本机成功发布作品，采集范围为0篇",
                    "business_result": {
                        "source": "creator_manager",
                        "captured_at": datetime.now(UTC).isoformat(),
                        "notes": [],
                        "count": 0,
                        "missing_note_ids": [],
                        "incomplete_note_ids": [],
                        "partial": False,
                        "inventory_complete": True,
                        "history_coverage": "local_published_notes",
                        "local_published_only": True,
                    },
                }
        command = [
            str(Path(sys.executable).with_name("python.exe"))
            if sys.platform == "win32"
            else sys.executable,
            "-X",
            "utf8",
            str(Path(__file__).with_name("cli.py")),
            "--account",
            slot,
            "collect-operations-metrics",
            "--source",
            "creator_manager",
            "--max-notes",
            str(parameters.get("max_notes") or 100),
        ]
        for note_id in note_ids:
            command.extend(["--note-id", str(note_id)])
        completed = self.command_runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(task.get("timeout_seconds") or 1200),
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            response = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return {"state": "FAILED", "message": "作品采集命令未返回有效结果"}
        local_task = response.get("task") or {}
        business = response.get("result") or {}
        state = str(local_task.get("state") or "FAILED").upper()
        if completed.returncode and state == "SUCCESS":
            state = "FAILED"
        if state == "SUCCESS" and business.get("partial"):
            state = "PARTIAL_SUCCESS"
        if state in {"SUCCESS", "PARTIAL_SUCCESS"} and local_only:
            business["local_published_only"] = True
            business["requested_note_ids"] = note_ids
            business["inventory_complete"] = not bool(business.get("missing_note_ids"))
            business["history_coverage"] = "local_published_notes"
        return {
            "state": state,
            "message": (
                f"本机 CLI 已采集 {len(business.get('notes') or [])} 篇作品"
                if state in {"SUCCESS", "PARTIAL_SUCCESS"}
                else str(
                    local_task.get("recommended_action")
                    or (response.get("error") or {}).get("message")
                    or "作品采集失败"
                )
            ),
            "business_result": business,
            "item_results": business.get("notes") or [],
            "error_code": local_task.get("error_code") or "",
            "evidence_index": {"local_task_id": local_task.get("task_id") or ""},
        }


def collect_creator_now(service: ManagerWorkerService, slot: str) -> dict[str, Any]:
    """Collect one account for an interactive manager request and return its receipt."""
    from account_manager import list_accounts
    from daily_passive_collection import eligibility
    from product_store import ProductStore
    from run_lock import RunLock

    account = next((item for item in list_accounts() if item.name == slot), None)
    if account is None:
        return {"success": False, "status": "BLOCKED", "message": "员工机账号槽位不存在"}
    worker = service._store.snapshot()
    status = eligibility(
        worker, ProductStore().snapshot(), slot,
        {"accounts": {slot: {"enabled": True}}},
    )
    if status not in {"READY", "DEFERRED"}:
        return {"success": False, "status": status, "message": f"账号当前状态：{status}"}
    lock = RunLock(str(service.root / "agent-locks" / (quote(slot, safe="") + ".lock")))
    if not lock.acquire(timeout=0.1):
        return {"success": False, "status": "DEFERRED", "message": "账号正在执行其他任务"}
    try:
        started_at = datetime.now(UTC).isoformat()
        task_id = f"creator-ui-{uuid.uuid4().hex}"
        outcome = DirectCreatorMetricsRunner(
            service.root, worker_snapshot=service._store.snapshot,
        )({
            "account_slot": slot,
            "capability": "collect-operations-metrics",
            "parameters": {"source": "creator_manager", "max_notes": 100},
            "timeout_seconds": 1200,
        })
        receipt = {
            "task_id": task_id,
            "dispatch_id": task_id,
            "remote_execution_id": task_id,
            "result_revision": 1,
            "machine_id": worker["config"]["machine_id"],
            "account_slot": slot,
            "platform": account.platform,
            "capability": "collect-operations-metrics",
            "source_type": "manager_direct",
            "state": outcome["state"],
            "started_at": started_at,
            "completed_at": datetime.now(UTC).isoformat(),
            "message": outcome.get("message") or "",
            "business_result": outcome.get("business_result") or {},
            "item_results": outcome.get("item_results") or [],
            "error_code": outcome.get("error_code") or "",
            "evidence_index": outcome.get("evidence_index") or {},
        }
        return {"success": True, "status": receipt["state"], "result": receipt}
    finally:
        lock.release()
