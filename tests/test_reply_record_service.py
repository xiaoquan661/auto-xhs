from __future__ import annotations

from scripts.operations_db import OperationsDatabase
from scripts.reply_record_service import ReplyRecordService


def test_comment_reply_record_keeps_original_and_sent_content(tmp_path) -> None:
    service = ReplyRecordService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )
    task = {
        "task_id": "reply-task-1",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "parameters": {
            "user_id": "user-1",
            "nickname": "访客",
            "original_content": "请问怎么报名？",
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


def test_private_message_reply_is_idempotent_by_task(tmp_path) -> None:
    service = ReplyRecordService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )
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
