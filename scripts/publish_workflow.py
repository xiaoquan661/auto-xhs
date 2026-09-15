"""Persistent multi-step publishing workflow shared by Agent CLI and WebUI monitoring."""

from __future__ import annotations

import uuid

from operation_event_service import OperationEventService
from operations_db import OperationsDatabase
from product_store import ProductStore
from published_post_service import PublishedPostService
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

VERIFIED_DRAFT_EVIDENCE = {
    "draft_success_toast",
    "draft_manager_readback",
}


class PublishWorkflowService:
    def __init__(
        self,
        store: ProductStore | None = None,
        *,
        operations_database: OperationsDatabase | None = None,
        operation_events: OperationEventService | None = None,
        published_posts: PublishedPostService | None = None,
    ) -> None:
        self.store = store or ProductStore()
        self.tasks = TaskService(self.store)
        database = operations_database or OperationsDatabase(root=self.store.root)
        self.operation_events = operation_events or OperationEventService(database)
        self.published_posts = published_posts or PublishedPostService(database)

    def begin(
        self,
        *,
        account: str,
        platform: str | None = None,
        capability: str,
        request_summary: str,
        preview: dict,
    ) -> dict:
        if capability not in PUBLISH_WORKFLOW_CAPABILITIES:
            raise ServiceError("INVALID_REQUEST", "未知发布工作流")
        self._validate_preview(capability, preview)
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
            platform=platform,
            capability=capability,
            request_summary=request_summary,
            parameters={"workflow_type": "publish", "stage": "preparing", "preview": preview},
        )
        return self.tasks.transition(task["task_id"], "RUNNING")

    def resume_preparation(self, task_id: str, *, account: str) -> dict:
        task = self._waiting_task(task_id, account=account)
        if task["state"] == "RUNNING":
            return task
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
        preview_revision = uuid.uuid4().hex
        if preview_updates:
            task = self.tasks.get(task_id)
            preview = {**(task.get("parameters") or {}).get("preview", {}), **preview_updates}
            self.tasks.update_parameters(
                task_id,
                preview=preview,
                stage=stage,
                preview_revision=preview_revision,
            )
        else:
            self.tasks.update_parameters(
                task_id,
                stage=stage,
                preview_revision=preview_revision,
            )
        if stage == "preview_ready":
            return self.tasks.get(task_id)
        return self.tasks.transition(task_id, "WAITING_APPROVAL", result_summary=summary)

    def prepare_publish(
        self,
        task_id: str,
        *,
        account: str,
        confirmed: bool = False,
        preview_revision: str = "",
        action: str = "click-publish",
    ) -> dict:
        task = self._waiting_task(task_id, account=account)
        stage = (task.get("parameters") or {}).get("stage")
        if stage == "publishing":
            raise ServiceError("INVALID_TASK_STATE", "发布已提交，请先核对执行结果", 409)
        if stage != "preview_ready":
            raise ServiceError("PREVIEW_NOT_READY", "发布表单尚未准备完成", 409)
        current_revision = str((task.get("parameters") or {}).get("preview_revision") or "")
        if preview_revision and preview_revision != current_revision:
            raise ServiceError(
                "PUBLISH_PREVIEW_CHANGED",
                "发布预览内容已变化，请刷新后重新核对",
                409,
            )
        from operations_policy import check_daily_context, check_operations_policy

        if action == "click-publish":
            check_daily_context(task.get("parameters") or {}, account=account)

        config = (task.get("parameters") or {}).get("operations_config") or self.store.get_setting(
            f"operations_config:{account}", {}
        )
        events = {
            event.get("task_id"): event
            for event in self.store.list("events")
            if event.get("account_slot") == account
        }
        history = [
            {**row, "result": (events.get(row["task_id"]) or {}).get("result") or {}}
            for row in self.tasks.list()
            if row["account_slot"] == account
        ]
        check_operations_policy(config, action, history)
        running = self.resume_preparation(task_id, account=account)
        return self.tasks.update_parameters(running["task_id"], stage="publishing")

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
        self._record_completion(completed, result=result, published=True)
        return completed

    def fail(
        self,
        task_id: str,
        exc: Exception,
        *,
        result_unknown: bool = False,
        published: bool = True,
    ) -> dict:
        state = "RESULT_UNKNOWN" if result_unknown else "FAILED"
        completed = self.tasks.transition(
            task_id,
            state,
            result_summary=str(exc),
            error_code=(
                "PUBLISH_PREVIEW_UNAVAILABLE"
                if getattr(exc, "code", None) == "PUBLISH_PREVIEW_UNAVAILABLE"
                else "PUBLISH_EXECUTION_ERROR"
            ),
            recommended_action=(
                "请先在小红书创作中心检查结果，不要直接重复发布"
                if result_unknown
                else "检查账号、素材和页面状态后重新创建发布任务"
            ),
        )
        self._record_completion(
            completed,
            result={"message": str(exc)},
            published=published,
        )
        return completed

    def complete_saved_draft(self, task_id: str, result: dict) -> dict:
        evidence = str(result.get("evidence") or "")
        if result.get("verified") and evidence in VERIFIED_DRAFT_EVIDENCE:
            completed = self.tasks.transition(
                task_id,
                "CANCELLED",
                result_summary="用户取消发布，内容已保存到小红书草稿箱",
            )
        else:
            completed = self.tasks.transition(
                task_id,
                "RESULT_UNKNOWN",
                result_summary="已触发保存草稿，但未能确认平台最终结果",
                error_code="PUBLISH_DRAFT_RESULT_UNKNOWN",
                recommended_action="请先在小红书草稿箱检查，不要直接重复保存",
            )
        self._record_completion(
            {**completed, "operation": "save-draft"},
            result=result,
            published=False,
        )
        return completed

    @staticmethod
    def _validate_preview(capability: str, preview: dict) -> None:
        kind = str(preview.get("kind") or "")
        expected_kind = {
            "fill-publish": "image",
            "fill-publish-video": "video",
            "long-article": "long_article",
        }[capability]
        if kind != expected_kind:
            raise ServiceError("PUBLISH_KIND_MISMATCH", "发布类型与任务能力不匹配", 409)
        if not str(preview.get("title") or "").strip():
            raise ServiceError("PUBLISH_CONTENT_REQUIRED", "发布标题不能为空", 409)
        if not str(preview.get("content") or "").strip():
            raise ServiceError("PUBLISH_CONTENT_REQUIRED", "发布正文不能为空", 409)
        assets = list(preview.get("assets") or [])
        if kind in {"image", "video"} and not assets:
            raise ServiceError("PUBLISH_ASSET_REQUIRED", "发布素材不能为空", 409)

    def _record_completion(
        self,
        task: dict,
        *,
        result: dict,
        published: bool,
    ) -> None:
        result = {
            **result,
            "published": (
                None
                if task["state"] == "RESULT_UNKNOWN"
                else published and task["state"] == "SUCCESS"
            ),
        }
        event_task = {**task, "operation": task.get("operation") or "publish"}
        self.tasks.record_event(event_task, task["task_id"], result=result)
        operation_event = self.operation_events.record(
            event_task,
            result=result,
            readback=(result.get("readback") or {}),
        )
        if published:
            self.published_posts.record(
                event_task,
                result=result,
                operation_event_id=operation_event["operation_event_id"],
            )

    def get(self, task_id: str, *, account: str) -> dict:
        task = self.tasks.get(task_id)
        if task["account_slot"] != account:
            raise ServiceError("CONFIRMATION_MISMATCH", "任务账号与当前 --account 不一致", 409)
        if task["capability"] not in PUBLISH_WORKFLOW_CAPABILITIES:
            raise ServiceError("INVALID_REQUEST", "该任务不是发布工作流", 409)
        return task

    def _waiting_task(self, task_id: str, *, account: str) -> dict:
        task = self.get(task_id, account=account)
        if task["state"] not in {"WAITING_APPROVAL", "RUNNING"}:
            raise ServiceError("INVALID_TASK_STATE", "只有准备中的发布任务可以继续", 409)
        return task
