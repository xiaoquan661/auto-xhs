"""Convert read-only comment observations into deduplicated inbound events."""

from __future__ import annotations

from collector_service import CollectorService
from inbound_event_service import InboundEventService
from service_errors import ServiceError


class CommentCollector:
    def __init__(
        self,
        events: InboundEventService,
        collectors: CollectorService,
    ) -> None:
        self.events = events
        self.collectors = collectors

    def ingest(
        self,
        *,
        account_slot: str,
        comments: list[dict],
        cursor_value: str = "",
        last_seen_time: str | None = None,
    ) -> dict:
        created: list[dict] = []
        existing: list[dict] = []
        for comment in comments:
            notification_id = str(comment.get("notification_id") or "").strip()
            comment_id = str(comment.get("comment_id") or comment.get("id") or "").strip()
            platform_event_id = notification_id or comment_id
            occurred_at = str(comment.get("occurred_at") or comment.get("create_time") or "")
            if not platform_event_id or not occurred_at:
                raise ServiceError(
                    "INVALID_COMMENT_EVENT",
                    "评论事件必须包含通知或评论标识以及 occurred_at",
                )
            result = self.events.record(
                account_slot=account_slot,
                event_type="note_comment",
                platform_event_id=platform_event_id,
                occurred_at=occurred_at,
                object_type="note",
                object_id=str(comment.get("feed_id") or ""),
                actor_user_id=str(comment.get("user_id") or ""),
                payload={
                    "notification_id": notification_id,
                    "comment_id": comment_id,
                    "feed_id": str(comment.get("feed_id") or ""),
                    "xsec_token": str(comment.get("xsec_token") or ""),
                    "user_id": str(comment.get("user_id") or ""),
                    "nickname": str(comment.get("nickname") or ""),
                    "content": str(comment.get("content") or ""),
                    "parent_comment_id": str(comment.get("parent_comment_id") or ""),
                    "parent_comment_content": str(
                        comment.get("parent_comment_content") or ""
                    ),
                    "note_title": str(comment.get("note_title") or ""),
                    "note_content": str(comment.get("note_content") or ""),
                    "note_tags": list(comment.get("note_tags") or []),
                    "source": str(comment.get("source") or ""),
                },
                classification=str(comment.get("classification") or ""),
            )
            (created if result["created"] else existing).append(result["event"])

        cursor = self.collectors.record_success(
            account_slot=account_slot,
            collector_type="note_comments",
            cursor_value=cursor_value,
            last_seen_time=last_seen_time,
        )
        return {
            "account_slot": account_slot,
            "observed_count": len(comments),
            "created_count": len(created),
            "duplicate_count": len(existing),
            "created_events": created,
            "cursor": cursor,
        }
