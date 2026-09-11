"""Convert read-only comment observations into deduplicated inbound events."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

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
        platform: str = "xiaohongshu",
        comments: list[dict],
        cursor_value: str = "",
        last_seen_time: str | None = None,
        task_id: str | None = None,
        scope: dict | None = None,
        coverage: str = "UNKNOWN",
        failures: list[dict] | None = None,
    ) -> dict:
        batch_id = uuid.uuid4().hex
        database = self.events.database
        now = datetime.now(UTC).isoformat()
        with database.transaction() as connection:
            connection.execute(
                """INSERT INTO comment_collection_batches
                (batch_id, account_slot, platform, task_id, started_at, scope_json, coverage)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    batch_id,
                    account_slot,
                    platform,
                    task_id,
                    now,
                    json.dumps(scope or {}, ensure_ascii=False),
                    coverage,
                ),
            )
        try:
            result = self._ingest(
                account_slot=account_slot,
                platform=platform,
                comments=comments,
                cursor_value=cursor_value,
                last_seen_time=last_seen_time,
                batch_id=batch_id,
            )
        except Exception as exc:
            with database.transaction() as connection:
                connection.execute(
                    """UPDATE comment_collection_batches SET finished_at=?, result_state='FAILED',
                    error_json=? WHERE batch_id=?""",
                    (
                        datetime.now(UTC).isoformat(),
                        json.dumps([{"message": str(exc)}], ensure_ascii=False),
                        batch_id,
                    ),
                )
            raise
        with database.transaction() as connection:
            connection.execute(
                """UPDATE comment_collection_batches SET finished_at=?, result_state=?,
                observed_count=?, new_count=?, error_json=? WHERE batch_id=?""",
                (
                    datetime.now(UTC).isoformat(),
                    "PARTIAL" if failures else "SUCCESS",
                    result["observed_count"],
                    result["created_count"],
                    json.dumps(failures or [], ensure_ascii=False),
                    batch_id,
                ),
            )
        return {**result, "batch_id": batch_id, "coverage": coverage}

    def _ingest(
        self,
        *,
        account_slot: str,
        platform: str = "xiaohongshu",
        comments: list[dict],
        cursor_value: str = "",
        last_seen_time: str | None = None,
        batch_id: str,
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
                platform=platform,
                event_type="note_comment",
                platform_event_id=platform_event_id,
                occurred_at=occurred_at,
                object_type="note",
                object_id=str(comment.get("feed_id") or ""),
                actor_user_id=str(comment.get("user_id") or ""),
                payload={
                    "notification_id": notification_id,
                    "target_url": str(comment.get("target_url") or ""),
                    "location_scope": str(comment.get("location_scope") or "note"),
                    "profile_url": str(comment.get("profile_url") or ""),
                    "comment_id": comment_id,
                    "feed_id": str(comment.get("feed_id") or ""),
                    "xsec_token": str(comment.get("xsec_token") or ""),
                    "user_id": str(comment.get("user_id") or ""),
                    "nickname": str(comment.get("nickname") or ""),
                    "content": str(comment.get("content") or ""),
                    "parent_comment_id": str(comment.get("parent_comment_id") or ""),
                    "parent_comment_content": str(comment.get("parent_comment_content") or ""),
                    "note_title": str(comment.get("note_title") or ""),
                    "note_content": str(comment.get("note_content") or ""),
                    "note_tags": list(comment.get("note_tags") or []),
                    "is_own_note": bool(comment.get("is_own_note", not notification_id)),
                    "source": str(comment.get("source") or ""),
                    "time_scope": comment.get("time_scope") or "platform",
                    "event_occurred_at": comment.get("event_occurred_at", occurred_at),
                    "captured_at": comment.get("captured_at"),
                },
                classification=str(comment.get("classification") or ""),
            )
            (created if result["created"] else existing).append(result["event"])
            with self.events.database.transaction() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO comment_batch_events VALUES (?, ?, ?)",
                    (
                        batch_id,
                        result["event"]["event_id"],
                        json.dumps(comment, ensure_ascii=False),
                    ),
                )

        cursor = self.collectors.record_success(
            account_slot=account_slot,
            platform=platform,
            collector_type="note_comments",
            cursor_value=cursor_value,
            last_seen_time=last_seen_time,
        )
        return {
            "account_slot": account_slot,
            "platform": platform,
            "observed_count": len(comments),
            "created_count": len(created),
            "duplicate_count": len(existing),
            "created_events": created,
            "existing_events": existing,
            "cursor": cursor,
        }
