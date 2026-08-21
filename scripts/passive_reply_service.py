"""Turn a passive inbound event into one reviewable reply task and draft."""

from __future__ import annotations

from approval_service import ApprovalService
from inbound_event_service import InboundEventService
from service_errors import ServiceError
from task_service import TaskService


class PassiveReplyService:
    def __init__(
        self,
        events: InboundEventService,
        tasks: TaskService,
        approvals: ApprovalService,
    ) -> None:
        self.events = events
        self.tasks = tasks
        self.approvals = approvals

    def create_draft(
        self,
        event_id: str,
        *,
        verified_uid: str,
        content: str,
        generation: dict | None = None,
    ) -> dict:
        event = self.events.get(event_id)
        if event["event_type"] not in {"note_comment", "private_message"}:
            raise ServiceError("INVALID_EVENT_TYPE", "该事件不能创建回复任务")
        if event.get("created_task_id"):
            task = self.tasks.get(event["created_task_id"])
            draft = next(
                (
                    item
                    for item in self.approvals.store.list("drafts")
                    if item.get("task_id") == task["task_id"]
                ),
                None,
            )
            return {"created": False, "event": event, "task": task, "draft": draft}

        payload = event.get("payload") or {}
        if event["event_type"] == "private_message":
            user_id = str(payload.get("user_id") or event.get("actor_user_id") or "")
            task = self.tasks.create(
                source="platform_event",
                source_type="platform_event",
                source_event_id=event_id,
                account_slot=event["account_slot"],
                capability="send-private-messages",
                operation="reviewed_reply",
                request_summary=f"回复新私信：{str(payload.get('content') or '')[:40]}",
                target_type="user",
                target_id=user_id,
                parameters={
                    "user_id": user_id,
                    "nickname": str(payload.get("nickname") or ""),
                    "original_content": str(payload.get("content") or ""),
                    "content": content.strip(),
                },
            )
            draft = self.approvals.create_draft(
                account_slot=event["account_slot"],
                verified_uid=verified_uid,
                action_type="send-private-messages",
                target_id=user_id,
                target_summary=(
                    f"{payload.get('nickname') or user_id or '用户'}："
                    f"{str(payload.get('content') or '')[:80]}"
                ),
                content=content,
                source_event_id=event_id,
                task_id=task["task_id"],
                metadata={"intelligent_reply": generation} if generation else {},
            )
            linked = self.events.attach_task(event_id, task["task_id"])
            return {"created": True, "event": linked, "task": task, "draft": draft}

        notification_id = str(payload.get("notification_id") or "")
        comment_id = str(payload.get("comment_id") or "")
        target_id = notification_id or comment_id or str(event["platform_event_id"])
        feed_id = str(payload.get("feed_id") or event.get("object_id") or "")
        task = self.tasks.create(
            source="platform_event",
            source_type="platform_event",
            source_event_id=event_id,
            account_slot=event["account_slot"],
            capability="reply-comment",
            request_summary=f"回复新评论：{str(payload.get('content') or '')[:40]}",
            target_type="notification" if notification_id else "comment",
            target_id=target_id,
            parameters={
                "feed_id": feed_id,
                "xsec_token": str(payload.get("xsec_token") or ""),
                "comment_id": comment_id,
                "notification_id": notification_id,
                "user_id": str(payload.get("user_id") or event.get("actor_user_id") or ""),
                "nickname": str(payload.get("nickname") or ""),
                "original_content": str(payload.get("content") or ""),
                "content": content.strip(),
            },
        )
        draft = self.approvals.create_draft(
            account_slot=event["account_slot"],
            verified_uid=verified_uid,
            action_type="reply-comment",
            target_id=target_id,
            target_summary=(
                f"{payload.get('nickname') or payload.get('user_id') or '用户'}："
                f"{str(payload.get('content') or '')[:80]}"
            ),
            content=content,
            source_event_id=event_id,
            task_id=task["task_id"],
            metadata={"intelligent_reply": generation} if generation else {},
        )
        linked = self.events.attach_task(event_id, task["task_id"])
        return {"created": True, "event": linked, "task": task, "draft": draft}
