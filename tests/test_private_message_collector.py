from __future__ import annotations

from scripts.collector_service import CollectorService
from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase
from scripts.private_message_collector import PrivateMessageCollector


def test_private_message_collector_deduplicates_same_observation(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    events = InboundEventService(database)
    collector = PrivateMessageCollector(events, CollectorService(database))
    message = {
        "message_id": "user-1:2:你好",
        "user_id": "user-1",
        "nickname": "小红",
        "content": "你好",
        "occurred_at": "2026-08-21T10:00:00+00:00",
        "message_count": 2,
        "context": [{"role": "peer", "content": "你好"}],
    }

    first = collector.ingest(account_slot="alpha", messages=[message])
    repeated = collector.ingest(account_slot="alpha", messages=[message])

    assert first["created_count"] == 1
    assert repeated["created_count"] == 0
    assert repeated["duplicate_count"] == 1
    event = events.list(event_type="private_message")[0]
    assert event["actor_user_id"] == "user-1"
    assert event["payload"]["content"] == "你好"


def test_human_owned_messages_are_recorded_without_returning_to_codex(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    events = InboundEventService(database)
    collector = PrivateMessageCollector(events, CollectorService(database))
    collector.conversations.handoff(
        "alpha",
        "xiaohongshu",
        "user-1",
        event_id="event-1",
        message_count=2,
    )

    result = collector.ingest(
        account_slot="alpha",
        messages=[
            {
                "message_id": "user-1:3:可以加个联系方式吗\n在吗",
                "user_id": "user-1",
                "nickname": "小红",
                "content": "可以加个联系方式吗\n在吗",
                "occurred_at": "2026-08-28T10:00:00+00:00",
                "message_count": 3,
                "context": [
                    {"role": "peer", "content": "可以加个联系方式吗"},
                    {"role": "peer", "content": "在吗"},
                ],
            }
        ],
    )

    assert result["created_count"] == 0
    assert result["human_owned_count"] == 1
    assert result["resumed_count"] == 0
    assert result["human_owned_events"][0]["handling_state"] == "HANDLED"


def test_human_reply_restores_codex_for_the_next_peer_turn(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    events = InboundEventService(database)
    collector = PrivateMessageCollector(events, CollectorService(database))
    collector.conversations.handoff(
        "alpha",
        "xiaohongshu",
        "user-1",
        event_id="event-1",
        message_count=2,
    )

    result = collector.ingest(
        account_slot="alpha",
        messages=[
            {
                "message_id": "user-1:4:你的桌面很好看",
                "user_id": "user-1",
                "nickname": "小红",
                "content": "你的桌面很好看",
                "occurred_at": "2026-08-28T10:01:00+00:00",
                "message_count": 4,
                "context": [
                    {"role": "peer", "content": "可以加个联系方式吗"},
                    {"role": "self", "content": "这个需要我人工处理一下"},
                    {"role": "peer", "content": "你的桌面很好看"},
                ],
                "peer_turn": ["你的桌面很好看"],
            }
        ],
    )

    assert result["created_count"] == 1
    assert result["human_owned_count"] == 0
    assert result["resumed_count"] == 1
    event = result["created_events"][0]
    assert event["handling_state"] == "NEW"
    assert event["payload"]["conversation_owner"] == "CODEX"
    assert event["payload"]["resume_after_message_count"] == 3


def test_self_latest_snapshot_restores_human_owned_conversation_without_event(
    tmp_path,
) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    events = InboundEventService(database)
    collector = PrivateMessageCollector(events, CollectorService(database))
    collector.conversations.handoff(
        "alpha",
        "xiaohongshu",
        "user-1",
        event_id="event-1",
        message_count=2,
    )

    result = collector.ingest(
        account_slot="alpha",
        messages=[],
        conversation_snapshots=[
            {
                "user_id": "user-1",
                "message_count": 3,
                "context": [
                    {"role": "peer", "content": "可以加个联系方式吗"},
                    {"role": "self", "content": "这个我来人工处理"},
                ],
                "latest_role": "self",
            }
        ],
    )

    assert result["created_count"] == 0
    assert result["resumed_count"] == 1
    assert result["codex_outbound_reconciliations"] == []
    conversation = collector.conversations.get("alpha", "xiaohongshu", "user-1")
    assert conversation["owner_state"] == "CODEX"
    assert conversation["resume_after_message_count"] == 3
    assert conversation["resume_source"] == "HUMAN_REPLY"
