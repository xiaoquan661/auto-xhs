from __future__ import annotations

import pytest

from scripts.operations_db import OperationsDatabase
from scripts.reply_record_service import ReplyRecordService


def test_comment_reply_record_keeps_original_and_sent_content(tmp_path) -> None:
    service = ReplyRecordService(OperationsDatabase(path=tmp_path / "operations.db"))
    task = {
        "task_id": "reply-task-1",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "parameters": {
            "user_id": "user-1",
            "nickname": "访客",
            "original_content": "请问怎么报名？",
            "rule_id": "rule-1",
            "rule_version": 4,
        },
        "state": "SUCCESS",
        "finished_at": "2026-08-26T10:00:00+00:00",
        "result_summary": "回复发送成功",
        "error_code": "",
    }
    event = {
        "event_id": "event-1",
        "event_type": "note_comment",
        "platform_event_id": "comment-1",
        "account_slot": "alpha",
        "object_id": "note-1",
        "actor_user_id": "user-1",
        "payload": {"comment_id": "comment-1", "feed_id": "note-1"},
    }
    draft = {
        "draft_id": "draft-1",
        "draft_revision_id": "revision-1",
        "verified_uid": "uid-alpha",
        "content": "请查看主页报名说明。",
    }

    record = service.record(
        task,
        draft=draft,
        approval={"approval_id": "approval-1"},
        source_event=event,
        result={"readback": {"reply_found": True}},
        operation_event_id="operation-1",
    )

    assert record["channel"] == "COMMENT_REPLY"
    assert record["original_content"] == "请问怎么报名？"
    assert record["reply_text"] == "请查看主页报名说明。"
    assert record["readback_state"] == "VERIFIED"
    assert record["rule_id"] == "rule-1"
    assert record["rule_version"] == 4


def test_comment_reply_record_accepts_notification_acknowledgement(tmp_path) -> None:
    service = ReplyRecordService(OperationsDatabase(path=tmp_path / "operations.db"))
    task = {
        "task_id": "reply-task-notification",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "parameters": {"content": "谢谢喜欢～"},
        "state": "SUCCESS",
    }
    event = {
        "event_id": "event-notification",
        "event_type": "note_comment",
        "platform_event_id": "notification-1",
        "account_slot": "alpha",
        "payload": {"content": "好喜欢"},
    }

    record = service.record(
        task,
        draft={"verified_uid": "uid-alpha", "content": "谢谢喜欢～"},
        source_event=event,
        result={"readback": "editor-closed"},
    )

    assert record["readback_state"] == "VERIFIED"
    assert record["readback"] == {"acknowledgement": "editor-closed"}


def test_private_message_reply_is_idempotent_by_task(tmp_path) -> None:
    service = ReplyRecordService(OperationsDatabase(path=tmp_path / "operations.db"))
    task = {
        "task_id": "reply-task-2",
        "account_slot": "alpha",
        "capability": "send-private-messages",
        "parameters": {"user_id": "user-2", "original_content": "你好"},
        "state": "RESULT_UNKNOWN",
        "finished_at": "2026-08-26T10:00:00+00:00",
        "result_summary": "发送后回读失败",
        "error_code": "PRIVATE_MESSAGE_RESULT_UNKNOWN",
    }
    event = {
        "event_id": "event-2",
        "event_type": "private_message",
        "platform_event_id": "message-2",
        "account_slot": "alpha",
        "object_id": "conversation-2",
        "actor_user_id": "user-2",
        "payload": {"message_id": "message-2", "content": "你好"},
    }
    values = {
        "draft": {
            "draft_id": "draft-2",
            "draft_revision_id": "revision-2",
            "verified_uid": "uid-alpha",
            "content": "你好呀",
        },
        "approval": {"approval_id": "approval-2"},
        "source_event": event,
        "result": {},
    }

    first = service.record(task, **values)
    second = service.record(task, **values)

    assert first["reply_record_id"] == second["reply_record_id"]
    assert first["channel"] == "PRIVATE_MESSAGE_REPLY"
    assert len(service.list()) == 1


@pytest.mark.parametrize(
    ("result_state", "outgoing_present", "send_state"),
    [
        ("SUCCESS", True, "CONFIRMED"),
        ("SUCCESS", False, "AWAITING_RECONCILIATION"),
        ("RESULT_UNKNOWN", True, "AWAITING_RECONCILIATION"),
    ],
)
def test_private_message_readback_projects_written_record(
    tmp_path,
    result_state,
    outgoing_present,
    send_state,
) -> None:
    service = ReplyRecordService(OperationsDatabase(path=tmp_path / "operations.db"))
    record = service.record(
        {
            "task_id": "private-reply",
            "account_slot": "alpha",
            "platform": "xiaohongshu",
            "state": result_state,
            "parameters": {"user_id": "peer", "rule_version": 4},
        },
        draft={"verified_uid": "owner", "content": "收到你的消息。"},
        source_event={"event_id": "inbound", "event_type": "private_message", "object_id": "peer"},
        result={
            "readback": {
                "user_id": "peer",
                "content": "收到你的消息。",
                "outgoing_message_present": outgoing_present,
            }
        },
    )
    written = service.get(record["reply_record_id"])
    assert written["result_state"] == result_state
    assert written["send_state"] == send_state
    assert bool(written["confirmed_at"]) == (send_state == "CONFIRMED")
    assert written["readback"]["outgoing_message_present"] is outgoing_present
    assert written["rule_version"] == 4


def test_successful_private_reply_reprojects_one_stored_readback(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    service = ReplyRecordService(database)
    record = service.record(
        {"task_id": "private-reply", "state": "SUCCESS", "account_slot": "alpha"},
        draft={"verified_uid": "owner", "content": "收到你的消息。"},
        source_event={"event_id": "inbound", "event_type": "private_message"},
        result={"readback": {"outgoing_message_present": True}},
    )
    other = service.record(
        {"task_id": "other", "state": "RESULT_UNKNOWN", "account_slot": "alpha"},
        draft={},
        source_event={"event_id": "other-inbound", "event_type": "private_message"},
    )
    with database.transaction() as connection:
        connection.execute(
            "UPDATE reply_records SET send_state='AWAITING_RECONCILIATION',confirmed_at=NULL "
            "WHERE reply_task_id='private-reply'"
        )

    corrected = service.reconcile_result_unknown("private-reply", readback={})
    assert corrected["reply_record_id"] == record["reply_record_id"]
    assert corrected["result_state"] == "SUCCESS"
    assert corrected["send_state"] == "CONFIRMED"
    assert corrected["confirmed_at"] == record["updated_at"]
    assert corrected["updated_at"] == record["updated_at"]
    assert corrected["readback"] == record["readback"]
    assert service.get(other["reply_record_id"])["send_state"] == "AWAITING_RECONCILIATION"
