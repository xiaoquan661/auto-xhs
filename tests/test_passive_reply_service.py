from __future__ import annotations

from scripts.approval_service import ApprovalService
from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase
from scripts.passive_reply_service import PassiveReplyService
from scripts.product_store import ProductStore
from scripts.task_service import TaskService


def _services(tmp_path):
    database = OperationsDatabase(path=tmp_path / "operations.db")
    store = ProductStore(tmp_path / "product")
    events = InboundEventService(database)
    tasks = TaskService(store)
    approvals = ApprovalService(store)
    return events, tasks, approvals, PassiveReplyService(events, tasks, approvals)


def test_new_comment_becomes_one_passive_reply_task_and_draft(tmp_path) -> None:
    events, tasks, _, replies = _services(tmp_path)
    event = events.record(
        account_slot="alpha",
        event_type="note_comment",
        platform_event_id="comment-1",
        occurred_at="2026-08-19T10:00:00+00:00",
        object_type="note",
        object_id="note-1",
        actor_user_id="user-1",
        payload={
            "comment_id": "comment-1",
            "feed_id": "note-1",
            "xsec_token": "token-1",
            "user_id": "user-1",
            "nickname": "小红",
            "content": "请问怎么报名？",
        },
    )["event"]

    result = replies.create_draft(
        event["event_id"],
        verified_uid="owner-1",
        content="你好，可以在主页查看报名说明。",
    )

    assert result["created"] is True
    assert result["task"]["source_type"] == "platform_event"
    assert result["task"]["source_event_id"] == event["event_id"]
    assert result["task"]["state"] == "WAITING_APPROVAL"
    assert result["draft"]["task_id"] == result["task"]["task_id"]
    assert result["draft"]["source_event_id"] == event["event_id"]
    assert events.get(event["event_id"])["handling_state"] == "TASK_CREATED"
    assert tasks.get(result["task"]["task_id"])["target_id"] == "comment-1"


def test_same_comment_does_not_create_a_second_reply_task(tmp_path) -> None:
    events, _, _, replies = _services(tmp_path)
    event = events.record(
        account_slot="alpha",
        event_type="note_comment",
        platform_event_id="comment-1",
        occurred_at="2026-08-19T10:00:00+00:00",
        payload={"comment_id": "comment-1", "feed_id": "note-1"},
    )["event"]

    first = replies.create_draft(
        event["event_id"],
        verified_uid="owner-1",
        content="第一次回复草稿",
    )
    repeated = replies.create_draft(
        event["event_id"],
        verified_uid="owner-1",
        content="第二次回复草稿",
    )

    assert first["created"] is True
    assert repeated["created"] is False
    assert repeated["task"]["task_id"] == first["task"]["task_id"]
    assert repeated["draft"]["draft_id"] == first["draft"]["draft_id"]
