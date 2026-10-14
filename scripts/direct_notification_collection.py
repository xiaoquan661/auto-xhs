"""Collect received comments and followers through fixed employee CLI calls."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from daily_passive_collection import DailyCollector, eligibility, observations
from run_lock import RunLock

if TYPE_CHECKING:
    from manager_worker_service import ManagerWorkerService


NOTIFICATION_COMMANDS = (
    ["collect-note-comments", "--max-notes", "100"],
    [
        "collect-operations-metrics",
        "--source",
        "notification_history",
        "--notification-kind",
        "new_followers",
    ],
)


def collect_notifications_now(service: ManagerWorkerService, slot: str) -> dict[str, Any]:
    """Read the selected account and return one manager-importable receipt."""
    from account_manager import list_accounts
    from product_store import ProductStore

    account = next((item for item in list_accounts() if item.name == slot), None)
    if account is None:
        return {"success": False, "status": "BLOCKED", "message": "员工机账号槽位不存在"}
    worker = service._store.snapshot()
    status = eligibility(
        worker,
        ProductStore().snapshot(),
        slot,
        {"accounts": {slot: {"enabled": True}}},
    )
    if status not in {"READY", "DEFERRED"}:
        return {"success": False, "status": status, "message": f"账号当前状态：{status}"}
    lock = RunLock(str(service.root / "agent-locks" / (quote(slot, safe="") + ".lock")))
    if not lock.acquire(timeout=0.1):
        return {"success": False, "status": "DEFERRED", "message": "账号正在执行其他任务"}
    try:
        started_at = datetime.now(UTC).isoformat()
        task_id = f"notification-ui-{uuid.uuid4().hex}"
        channels = []
        incremental = []
        for command in NOTIFICATION_COMMANDS:
            value = DailyCollector.collect(slot, command)
            state = str((value.get("task") or {}).get("state") or "FAILED").upper()
            business = value.get("result") or {}
            observed_count = (
                int((business.get("inbound_events") or {}).get("observed_count") or 0)
                if command[0] == "collect-note-comments"
                else len((business.get("growth_notifications") or {}).get("events") or [])
            )
            channels.append(
                {
                    "capability": command[0],
                    "metric": "received_comments"
                    if command[0] == "collect-note-comments"
                    else "new_followers",
                    "state": state,
                    "observed_count": observed_count,
                    "captured_at": business.get("captured_at"),
                    "message": (value.get("error") or {}).get("message", "")
                    if isinstance(value.get("error"), dict)
                    else str(value.get("error") or ""),
                }
            )
            incremental.extend(observations(value))
        states = [row["state"] for row in channels]
        result_state = (
            "SUCCESS"
            if all(state == "SUCCESS" for state in states)
            else "PARTIAL_SUCCESS"
            if any(state == "SUCCESS" for state in states) or incremental
            else "FAILED"
        )
        completed_at = datetime.now(UTC).isoformat()
        receipt = {
            "task_id": task_id,
            "dispatch_id": task_id,
            "remote_execution_id": task_id,
            "result_revision": 1,
            "machine_id": worker["config"]["machine_id"],
            "account_slot": slot,
            "platform": account.platform,
            "capability": "collect-notifications",
            "source_type": "manager_direct",
            "state": result_state,
            "started_at": started_at,
            "completed_at": completed_at,
            "message": {
                "SUCCESS": "通知采集完成",
                "PARTIAL_SUCCESS": "通知采集部分完成",
                "FAILED": "通知采集失败",
            }[result_state],
            "business_result": {
                "source": "notification_history",
                "captured_at": completed_at,
                "channels": channels,
            },
            "item_results": channels,
            "incremental_data": incremental,
            "incremental_data_status": "observed",
        }
        return {"success": True, "status": result_state, "result": receipt}
    finally:
        lock.release()
