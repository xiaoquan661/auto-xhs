"""Persistent multi-step publishing workflow shared by Agent CLI and WebUI monitoring."""

from __future__ import annotations

from product_store import ProductStore
from service_errors import ServiceError
from task_service import FINAL_STATES, TaskService

PUBLISH_WORKFLOW_CAPABILITIES = {
    "fill-publish",
    "fill-publish-video",
    "long-article",
}

VERIFIED_PUBLISH_EVIDENCE = {
    "platform_response",
    "success_toast",
    "success_page",
    "note_manager_readback",
}


class PublishWorkflowService:
    def __init__(self, store: ProductStore | None = None) -> None:
        self.store = store or ProductStore()
        self.tasks = TaskService(self.store)

    def begin(
        self,
        *,
        account: str,
        capability: str,
        request_summary: str,
        preview: dict,
    ) -> dict:
        if capability not in PUBLISH_WORKFLOW_CAPABILITIES:
            raise ServiceError("INVALID_REQUEST", "未知发布工作流")
        active = next(
            (
                task
                for task in self.tasks.list()
                if task["account_slot"] == account
                and task["capability"] in PUBLISH_WORKFLOW_CAPABILITIES
                and task["state"] not in FINAL_STATES
            ),
            None,
        )
        if active:
            raise ServiceError(
                "PUBLISH_WORKFLOW_ACTIVE",
                f"该账号已有未完成发布任务 {active['task_id']}，请先完成或保存草稿",
                409,
            )
        task = self.tasks.create_workflow(
            source="agent",
            account_slot=account,
            capability=capability,
            request_summary=request_summary,
            parameters={"workflow_type": "publish", "stage": "preparing", "preview": preview},
        )
        return self.tasks.transition(task["task_id"], "RUNNING")

    def resume_preparation(self, task_id: str, *, account: str) -> dict:
        task = self._waiting_task(task_id, account=account)
        queued = self.tasks.transition(task_id, "QUEUED")
        return self.tasks.transition(queued["task_id"], "RUNNING")

    def wait_for_confirmation(
        self,
        task_id: str,
        *,
        summary: str,
        stage: str = "preview_ready",
        preview_updates: dict | None = None,
    ) -> dict:
        if preview_updates:
            task = self.tasks.get(task_id)
            preview = {**(task.get("parameters") or {}).get("preview", {}), **preview_updates}
            self.tasks.update_parameters(task_id, preview=preview, stage=stage)
        else:
            self.tasks.update_parameters(task_id, stage=stage)
        return self.tasks.transition(task_id, "WAITING_APPROVAL", result_summary=summary)

    def prepare_publish(self, task_id: str, *, account: str, confirmed: bool) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "发布必须在用户确认预览后提供 --confirm", 409)
        task = self._waiting_task(task_id, account=account)
        stage = (task.get("parameters") or {}).get("stage")
        if stage != "preview_ready":
            raise ServiceError("PREVIEW_NOT_READY", "发布任务尚未进入最终预览确认阶段", 409)
        return self.resume_preparation(task_id, account=account)

    def complete_publish(self, task_id: str, result: dict) -> dict:
        evidence = str(result.get("evidence") or "")
        if result.get("verified") and evidence in VERIFIED_PUBLISH_EVIDENCE:
            completed = self.tasks.transition(
                task_id,
                "SUCCESS",
                result_summary="发布成功，平台响应已核验",
            )
        else:
            completed = self.tasks.transition(
                task_id,
                "RESULT_UNKNOWN",
                result_summary="发布动作已触发，但未能确认平台最终结果",
                error_code="PUBLISH_RESULT_UNKNOWN",
                recommended_action="请先在小红书创作中心检查，不要直接重复发布",
            )
        self.tasks.record_event(completed, task_id, result=result)
        return completed

    def fail(self, task_id: str, exc: Exception, *, result_unknown: bool = False) -> dict:
        state = "RESULT_UNKNOWN" if result_unknown else "FAILED"
        completed = self.tasks.transition(
            task_id,
            state,
            result_summary=str(exc),
            error_code="PUBLISH_EXECUTION_ERROR",
            recommended_action=(
                "请先在小红书创作中心检查结果，不要直接重复发布"
                if result_unknown
                else "检查账号、素材和页面状态后重新创建发布任务"
            ),
        )
        self.tasks.record_event(completed, task_id)
        return completed

    def complete_saved_draft(self, task_id: str) -> dict:
        completed = self.tasks.transition(
            task_id,
            "CANCELLED",
            result_summary="用户取消发布，内容已保存到小红书草稿箱",
        )
        self.tasks.record_event(completed, task_id, result={"saved_as_draft": True})
        return completed

    def get(self, task_id: str, *, account: str) -> dict:
        task = self.tasks.get(task_id)
        if task["account_slot"] != account:
            raise ServiceError("CONFIRMATION_MISMATCH", "任务账号与当前 --account 不一致", 409)
        if task["capability"] not in PUBLISH_WORKFLOW_CAPABILITIES:
            raise ServiceError("INVALID_REQUEST", "该任务不是发布工作流", 409)
        return task

    def _waiting_task(self, task_id: str, *, account: str) -> dict:
        task = self.get(task_id, account=account)
        if task["state"] != "WAITING_APPROVAL":
            raise ServiceError("INVALID_TASK_STATE", "只有待确认的发布任务可以继续", 409)
        return task
