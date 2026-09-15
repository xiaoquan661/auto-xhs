"""Convert private-message observations into deduplicated inbound events."""

from __future__ import annotations

from collector_service import CollectorService
from conversation_ownership_service import ConversationOwnershipService
from inbound_event_service import InboundEventService
from service_errors import ServiceError


class PrivateMessageCollector:
    def __init__(
        self,
        events: InboundEventService,
        collectors: CollectorService,
        conversations: ConversationOwnershipService | None = None,
    ) -> None:
        self.events = events
        self.collectors = collectors
        self.conversations = conversations or ConversationOwnershipService(events.database)

    def ingest(
        self,
        *,
        account_slot: str,
        platform: str = "xiaohongshu",
        messages: list[dict],
        conversation_snapshots: list[dict] | None = None,
        cursor_value: str = "",
        last_seen_time: str | None = None,
    ) -> dict:
        created: list[dict] = []
        existing: list[dict] = []
        resumed_count = 0
        ownership_by_user: dict[str, dict] = {}
        resume_events: list[dict] = []
        reconciliations: list[dict] = []
        snapshots = list(conversation_snapshots or [])
        for snapshot in snapshots:
            user_id = str(snapshot.get("user_id") or "").strip()
            if not user_id:
                continue
            ownership = self.conversations.observe(
                account_slot=account_slot,
                platform=platform,
                user_id=user_id,
                message_count=int(snapshot.get("message_count") or 0),
                context=list(snapshot.get("context") or []),
            )
            ownership_by_user[user_id] = ownership
            if ownership.get("resumed"):
                resumed_count += 1
                resume_events.append(ownership)
            if ownership.get("reconciliation"):
                reconciliations.append(dict(ownership["reconciliation"]))
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
            context = list(message.get("context") or [])
            ownership = ownership_by_user.get(user_id)
            if ownership is None:
                ownership = self.conversations.observe(
                    account_slot=account_slot,
                    platform=platform,
                    user_id=user_id,
                    message_count=int(message.get("message_count") or 0),
                    context=context,
                )
                if ownership.get("resumed"):
                    resumed_count += 1
                    resume_events.append(ownership)
                if ownership.get("reconciliation"):
                    reconciliations.append(dict(ownership["reconciliation"]))
            result = self.events.record(
                account_slot=account_slot,
                platform=platform,
                event_type="private_message",
                platform_event_id=message_id,
                occurred_at=occurred_at,
                object_type="conversation",
                object_id=user_id,
                actor_user_id=user_id,
                payload={
                    "user_id": user_id,
                    "nickname": str(message.get("nickname") or ""),
                    "thread_url": str(message.get("thread_url") or ""),
                    "read_status": str(message.get("read_status") or "UNKNOWN"),
                    "read_status_observed_at": str(message.get("read_status_observed_at") or ""),
                    "content": content,
                    "display_time": str(message.get("display_time") or ""),
                    "time_scope": message.get("time_scope") or "first_observed",
                    "metric_semantics": "conversation_turn",
                    "observed_at": occurred_at,
                    "event_occurred_at": message.get("event_occurred_at"),
                    "platform_message_id": message.get("platform_message_id"),
                    "message_count": int(message.get("message_count") or 0),
                    "context": context,
                    "peer_turn": list(message.get("peer_turn") or [content]),
                    "source": str(message.get("source") or "chat_inbox"),
                    "conversation_owner": ownership["owner_state"],
                    "handled_through_message_count": ownership["handled_through_message_count"],
                    "resume_after_message_count": ownership["resume_after_message_count"],
                },
                classification="incoming_text",
            )
            if not result["created"]:
                existing.append(result["event"])
            else:
                created.append(result["event"])

        cursor = self.collectors.record_success(
            account_slot=account_slot,
            platform=platform,
            collector_type="private_messages",
            cursor_value=cursor_value,
            last_seen_time=last_seen_time,
        )
        return {
            "account_slot": account_slot,
            "platform": platform,
            "observed_count": len(messages),
            "observed_conversation_count": len(snapshots),
            "created_count": len(created),
            "duplicate_count": len(existing),
            "resumed_count": resumed_count,
            "resume_events": resume_events,
            "codex_outbound_reconciliations": reconciliations,
            "created_events": created,
            "existing_events": existing,
            "cursor": cursor,
        }
