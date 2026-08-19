from __future__ import annotations

from scripts.collector_service import CollectorService
from scripts.comment_collector import CommentCollector
from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase


def _collector(tmp_path) -> CommentCollector:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    return CommentCollector(
        InboundEventService(database),
        CollectorService(database),
    )


def test_comment_collection_creates_only_new_inbound_events(tmp_path) -> None:
    collector = _collector(tmp_path)
    comments = [
        {
            "comment_id": "comment-1",
            "feed_id": "note-1",
            "user_id": "user-1",
            "nickname": "小红",
            "content": "请问怎么报名？",
            "occurred_at": "2026-08-19T10:00:00+00:00",
            "xsec_token": "token-1",
        }
    ]

    first = collector.ingest(
        account_slot="alpha",
        comments=comments,
        cursor_value="comment-1",
    )
    repeated = collector.ingest(
        account_slot="alpha",
        comments=comments,
        cursor_value="comment-1",
    )

    assert first["created_count"] == 1
    assert first["duplicate_count"] == 0
    assert repeated["created_count"] == 0
    assert repeated["duplicate_count"] == 1
    assert repeated["cursor"]["cursor_value"] == "comment-1"
