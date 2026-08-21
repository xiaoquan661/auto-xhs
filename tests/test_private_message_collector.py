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
