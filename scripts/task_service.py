"""Task state and recovery rules for the local V1 product."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from capability_registry import get_operation_policy
from product_store import ProductStore
from service_errors import ServiceError

TASK_STATES = {
    "QUEUED",
    "RUNNING",
    "WAITING_APPROVAL",
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
    "RESULT_UNKNOWN",
}
FINAL_STATES = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "CANCELLED", "RESULT_UNKNOWN"}
_TRANSITIONS = {
    "QUEUED": {"RUNNING", "BLOCKED", "CANCELLED"},
    "WAITING_APPROVAL": {"QUEUED", "BLOCKED", "CANCELLED"},
    "RUNNING": {
        "WAITING_APPROVAL",
        "SUCCESS",
        "PARTIAL_SUCCESS",
        "FAILED",
        "BLOCKED",
        "CANCELLED",
        "RESULT_UNKNOWN",
    },
    "BLOCKED": {"QUEUED", "CANCELLED"},
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class TaskService:
    def __init__(self, store: ProductStore) -> None:
        self.store = store

    def create(
        self,
        *,
        source: str,
        account_slot: str,
        capability: str,
        request_summary: str,
        operation: str | None = None,
        parameters: dict | None = None,
    ) -> dict:
        try:
            policy = get_operation_policy(capability, operation)
        except KeyError as exc:
            raise ServiceError("CAPABILITY_NOT_FOUND", str(exc), 404) from exc
        if not policy.enabled_in_v1:
            raise ServiceError(
                "CAPABILITY_DISABLED",
                f"{capability} 不属于当前 V1 开放能力",
                409,
            )
        if policy.requires_target_account and not account_slot.strip():
            raise ServiceError("INVALID_REQUEST", "必须明确目标账号槽位")
        task_id = uuid.uuid4().hex
        now = utc_now()
        task = {
            "task_id": task_id,
            "source": source.strip() or "unknown",
            "account_slot": account_slot.strip(),
            "capability": capability,
            "operation": operation,
            "risk_level": str(policy.risk_level),
            "request_summary": request_summary.strip(),
            "parameters": dict(parameters or {}),
            "state": "WAITING_APPROVAL" if policy.requires_confirmation else "QUEUED",
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "result_summary": "",
            "error_code": "",
            "recommended_action": "",
        }
        return self.store.put("tasks", task_id, task)

    def create_workflow(
        self,
        *,
        source: str,
        account_slot: str,
        capability: str,
        request_summary: str,
        parameters: dict | None = None,
    ) -> dict:
        """Create a queued multi-step task whose confirmation happens mid-workflow."""
        task = self.create(
            source=source,
            account_slot=account_slot,
            capability=capability,
            request_summary=request_summary,
            parameters=parameters,
        )
        if task["state"] == "WAITING_APPROVAL":
            task = self.store.update(
                "tasks",
                task["task_id"],
                lambda current: {**current, "state": "QUEUED"},
            )
        return task

    def get(self, task_id: str) -> dict:
        task = self.store.get("tasks", task_id)
        if task is None:
            raise ServiceError("NOT_FOUND", "任务不存在", 404)
        return task

    def list(self) -> list[dict]:
        return sorted(
            self.store.list("tasks"),
            key=lambda item: item["created_at"],
            reverse=True,
        )

    def transition(
        self,
        task_id: str,
        state: str,
        *,
        result_summary: str = "",
        error_code: str = "",
        recommended_action: str = "",
    ) -> dict:
        if state not in TASK_STATES:
            raise ServiceError("INVALID_REQUEST", f"未知任务状态: {state}")

        def mutate(task: dict) -> dict:
            current = task["state"]
            if state not in _TRANSITIONS.get(current, set()):
                raise ServiceError(
                    "INVALID_TASK_STATE",
                    f"任务不能从 {current} 进入 {state}",
                    409,
                )
            now = utc_now()
            task["state"] = state
            if state == "QUEUED":
                task["started_at"] = None
                task["finished_at"] = None
            if state == "RUNNING":
                task["started_at"] = now
                task["finished_at"] = None
            if state == "WAITING_APPROVAL":
                task["finished_at"] = None
            if state in FINAL_STATES or state == "BLOCKED":
                task["finished_at"] = now
            task["result_summary"] = result_summary
            task["error_code"] = error_code
            task["recommended_action"] = recommended_action
            return task

        updated = self.store.update("tasks", task_id, mutate)
        if updated is None:
            raise ServiceError("NOT_FOUND", "任务不存在", 404)
        return updated

    def update_parameters(self, task_id: str, **changes) -> dict:
        def mutate(task: dict) -> dict:
            task["parameters"] = {**(task.get("parameters") or {}), **changes}
            return task

        updated = self.store.update("tasks", task_id, mutate)
        if updated is None:
            raise ServiceError("NOT_FOUND", "任务不存在", 404)
        return updated

    def record_event(self, task: dict, target_key: str, *, result=None) -> dict:
        event_id = uuid.uuid4().hex
        event = {
            "event_id": event_id,
            "task_id": task["task_id"],
            "account_slot": task["account_slot"],
            "capability": task["capability"],
            "risk_level": task["risk_level"],
            "target_key": target_key,
            "state": task["state"],
            "started_at": task["started_at"],
            "finished_at": task["finished_at"],
            "result_summary": task["result_summary"],
            "error_code": task["error_code"],
            "recommended_action": task["recommended_action"],
            "result": result or {},
        }
        return self.store.put("events", event_id, event)

    def claim_for_execution(self, task_id: str) -> dict:
        """Atomically mark a queued task running when its account is idle."""

        def mutate(state: dict) -> dict:
            task = state["tasks"].get(task_id)
            if task is None:
                raise ServiceError("NOT_FOUND", "任务不存在", 404)
            if task["state"] != "QUEUED":
                raise ServiceError(
                    "INVALID_TASK_STATE",
                    "只有排队中的任务可以执行",
                    409,
                )

            running = next(
                (
                    item
                    for other_id, item in state["tasks"].items()
                    if other_id != task_id
                    and item.get("account_slot") == task.get("account_slot")
                    and item.get("state") == "RUNNING"
                ),
                None,
            )
            now = utc_now()
            if running:
                task["state"] = "BLOCKED"
                task["finished_at"] = now
                task["result_summary"] = "同一账号已有任务正在执行"
                task["error_code"] = "ACCOUNT_BUSY"
                task["recommended_action"] = (
                    f"等待任务 {running['task_id'][:8]} 完成后再重试"
                )
            else:
                task["state"] = "RUNNING"
                task["started_at"] = now
                task["finished_at"] = None
                task["result_summary"] = ""
                task["error_code"] = ""
                task["recommended_action"] = ""
            return dict(task)

        return self.store.mutate(mutate)

    def recover_interrupted(self) -> list[dict]:
        recovered: list[dict] = []
        for task in self.list():
            if task["state"] != "RUNNING":
                continue
            policy = get_operation_policy(task["capability"], task.get("operation"))
            target = "RESULT_UNKNOWN" if policy.requires_result_verification else "FAILED"
            recovered.append(
                self.transition(
                    task["task_id"],
                    target,
                    result_summary="本地服务在任务执行期间中断",
                    error_code="SERVICE_INTERRUPTED",
                    recommended_action=(
                        "先回读平台当前状态，再决定是否重新执行"
                        if target == "RESULT_UNKNOWN"
                        else "检查账号状态后重新发起任务"
                    ),
                )
            )
        return recovered
