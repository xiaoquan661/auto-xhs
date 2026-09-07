from __future__ import annotations

import pytest

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


def test_cursor_retry_keeps_event_persisted_before_progress_advances(
    tmp_path,
    monkeypatch,
) -> None:
    collector = _collector(tmp_path)
    comment = {
        "comment_id": "comment-retry-1",
        "occurred_at": "2026-08-28T10:00:00+00:00",
        "content": "游标写入前已取得的评论",
    }
    original_record_success = collector.collectors.record_success

    def fail_cursor_write(**_values: object) -> dict:
        raise RuntimeError("cursor write interrupted")

    monkeypatch.setattr(collector.collectors, "record_success", fail_cursor_write)
    with pytest.raises(RuntimeError, match="cursor write interrupted"):
        collector.ingest(
            account_slot="alpha",
            comments=[comment],
            cursor_value="comment-retry-1",
        )

    persisted = collector.events.list(account_slot="alpha")
    assert len(persisted) == 1
    assert collector.collectors.get("alpha", "note_comments") is None

    monkeypatch.setattr(
        collector.collectors,
        "record_success",
        original_record_success,
    )
    retried = collector.ingest(
        account_slot="alpha",
        comments=[comment],
        cursor_value="comment-retry-1",
    )

    assert retried["created_count"] == 0
    assert retried["duplicate_count"] == 1
    assert retried["cursor"]["cursor_value"] == "comment-retry-1"
    assert len(collector.events.list(account_slot="alpha")) == 1
