"""Convert private-message observations into deduplicated inbound events."""

from __future__ import annotations

from collector_service import CollectorService
from inbound_event_service import InboundEventService
from service_errors import ServiceError


class PrivateMessageCollector:
    def __init__(self, events: InboundEventService, collectors: CollectorService) -> None:
        self.events = events
        self.collectors = collectors

    def ingest(
        self,
        *,
        account_slot: str,
        messages: list[dict],
        cursor_value: str = "",
        last_seen_time: str | None = None,
    ) -> dict:
        created: list[dict] = []
        existing: list[dict] = []
        for message in messages:
            message_id = str(message.get("message_id") or "").strip()
            user_id = str(message.get("user_id") or "").strip()
            content = str(message.get("content") or "").strip()
            occurred_at = str(message.get("occurred_at") or "").strip()
            if not message_id or not user_id or not content or not occurred_at:
                raise ServiceError(
                    "INVALID_PRIVATE_MESSAGE_EVENT",
                    "私信事件必须包含消息标识、发件人、正文和时间",
                )
            result = self.events.record(
                account_slot=account_slot,
                event_type="private_message",
                platform_event_id=message_id,
                occurred_at=occurred_at,
                object_type="conversation",
                object_id=user_id,
                actor_user_id=user_id,
                payload={
                    "user_id": user_id,
                    "nickname": str(message.get("nickname") or ""),
                    "content": content,
                    "display_time": str(message.get("display_time") or ""),
                    "message_count": int(message.get("message_count") or 0),
                    "context": list(message.get("context") or []),
                    "source": str(message.get("source") or "chat_inbox"),
                },
                classification="incoming_text",
            )
            (created if result["created"] else existing).append(result["event"])

        cursor = self.collectors.record_success(
            account_slot=account_slot,
            collector_type="private_messages",
            cursor_value=cursor_value,
            last_seen_time=last_seen_time,
        )
        return {
            "account_slot": account_slot,
            "observed_count": len(messages),
            "created_count": len(created),
            "duplicate_count": len(existing),
            "created_events": created,
            "cursor": cursor,
        }
