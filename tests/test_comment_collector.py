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
            "parent_comment_id": "parent-1",
            "parent_comment_content": "活动什么时候开始？",
            "note_title": "周末活动",
            "note_content": "周六下午举行。",
            "note_tags": ["活动"],
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
    payload = first["created_events"][0]["payload"]
    assert payload["note_title"] == "周末活动"
    assert payload["parent_comment_content"] == "活动什么时候开始？"


def test_notification_id_is_used_for_dedup_without_inventing_a_comment_id(tmp_path) -> None:
    collector = _collector(tmp_path)
    comment = {
        "notification_id": "notification-1",
        "comment_id": "",
        "occurred_at": "2026-08-21T10:00:00+00:00",
        "nickname": "学远",
        "content": "好看",
        "source": "notification",
    }

    result = collector.ingest(account_slot="alpha", comments=[comment])

    event = result["created_events"][0]
    assert event["platform_event_id"] == "notification-1"
    assert event["payload"]["notification_id"] == "notification-1"
    assert event["payload"]["comment_id"] == ""
