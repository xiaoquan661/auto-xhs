from __future__ import annotations

import pytest

from scripts.conversation_ownership_service import (
    ConversationOwnershipService,
    OWNER_CODEX,
    OWNER_HUMAN,
    RESUME_CODEX_OUTBOUND,
)
from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase
from scripts.reply_cycle_service import ReplyCycleService
from service_errors import ServiceError


def _service(tmp_path) -> ConversationOwnershipService:
    return ConversationOwnershipService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )


def test_handoff_and_human_reply_create_a_new_codex_boundary(tmp_path) -> None:
    service = _service(tmp_path)
    service.observe(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="user-1",
        message_count=2,
        context=[
            {"role": "self", "content": "你好"},
            {"role": "peer", "content": "可以加个联系方式吗"},
        ],
    )

    handed_off = service.handoff(
        "alpha",
        "xiaohongshu",
        "user-1",
        event_id="event-1",
        message_count=2,
    )
    still_human = service.observe(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="user-1",
        message_count=3,
        context=[
            {"role": "self", "content": "你好"},
            {"role": "peer", "content": "可以加个联系方式吗"},
            {"role": "peer", "content": "在吗"},
        ],
    )
    resumed = service.observe(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="user-1",
        message_count=5,
        context=[
            {"role": "peer", "content": "可以加个联系方式吗"},
            {"role": "peer", "content": "在吗"},
            {"role": "self", "content": "这个需要我人工处理一下"},
            {"role": "peer", "content": "你的桌面很好看"},
        ],
    )

    assert handed_off["owner_state"] == OWNER_HUMAN
    assert handed_off["handled_through_message_count"] == 2
    assert still_human["owner_state"] == OWNER_HUMAN
    assert still_human["resumed"] is False
    assert resumed["owner_state"] == OWNER_CODEX
    assert resumed["resumed"] is True
    assert resumed["resume_after_message_count"] == 4
    assert resumed["handoff_event_id"] == ""


def test_confirmed_manual_resume_uses_latest_observed_message_as_boundary(tmp_path) -> None:
    service = _service(tmp_path)
    service.handoff(
        "alpha",
        "xiaohongshu",
        "user-1",
        event_id="event-1",
        message_count=2,
    )
    service.observe(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="user-1",
        message_count=4,
        context=[
            {"role": "peer", "content": "消息1"},
            {"role": "peer", "content": "消息2"},
        ],
    )

    resumed = service.resume(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="user-1",
    )

    assert resumed["owner_state"] == OWNER_CODEX
    assert resumed["handled_through_message_count"] == 4
    assert resumed["resume_after_message_count"] == 4


def test_resume_rejects_unknown_conversation(tmp_path) -> None:
    with pytest.raises(ServiceError) as exc_info:
        _service(tmp_path).resume(
            account_slot="alpha",
            platform="xiaohongshu",
            user_id="missing",
        )

    assert exc_info.value.code == "NOT_FOUND"


def test_backfill_restores_latest_pending_handoff_without_stale_context_pending(
    tmp_path,
) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    events = InboundEventService(database)
    cycles = ReplyCycleService(database)
    manual_event = events.record(
        account_slot="alpha",
        event_type="private_message",
        platform_event_id="user-1:2:需要人工",
        occurred_at="2026-08-28T10:00:00+00:00",
        object_type="conversation",
        object_id="user-1",
        payload={"message_count": 2},
    )["event"]
    stale_event = events.record(
        account_slot="alpha",
        event_type="private_message",
        platform_event_id="user-2:4:新消息",
        occurred_at="2026-08-28T10:01:00+00:00",
        object_type="conversation",
        object_id="user-2",
        payload={"message_count": 4},
    )["event"]
    cycle = cycles.start(account_slot="alpha")
    cycles.record_decision(
        cycle_id=cycle["cycle_id"],
        event=manual_event,
        decision="pending",
        reason="manual_category",
    )
    cycles.record_decision(
        cycle_id=cycle["cycle_id"],
        event=stale_event,
        decision="pending",
        reason="conversation_changed",
    )

    service = ConversationOwnershipService(database)
    service.backfill_latest_decisions()

    assert service.get("alpha", "xiaohongshu", "user-1")["owner_state"] == OWNER_HUMAN
    assert service.get("alpha", "xiaohongshu", "user-2") is None


def test_result_unknown_reply_text_is_attributed_to_codex_outbound(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    events = InboundEventService(database)
    cycles = ReplyCycleService(database)
    event = events.record(
        account_slot="alpha",
        event_type="private_message",
        platform_event_id="user-1:2:你好",
        occurred_at="2026-08-28T10:00:00+00:00",
        object_type="conversation",
        object_id="user-1",
        payload={"message_count": 2},
    )["event"]
    cycle = cycles.start(account_slot="alpha")
    cycles.record_decision(
        cycle_id=cycle["cycle_id"],
        event=event,
        decision="pending",
        reason="result_unknown",
        generation={"reply": "你好呀～"},
        reply_task_id="task-1",
        result_state="RESULT_UNKNOWN",
    )
    service = ConversationOwnershipService(database)
    service.handoff(
        "alpha",
        "xiaohongshu",
        "user-1",
        event_id=event["event_id"],
        message_count=2,
    )

    observed = service.observe(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="user-1",
        message_count=3,
        context=[
            {"role": "peer", "content": "你好"},
            {"role": "self", "content": "你好呀～"},
        ],
    )

    assert observed["owner_state"] == OWNER_CODEX
    assert observed["resume_source"] == RESUME_CODEX_OUTBOUND
    assert observed["matched_reply_task_id"] == "task-1"
    assert observed["reconciliation"] == {
        "inbound_event_id": event["event_id"],
        "reply_task_id": "task-1",
        "message_count": 3,
        "content": "你好呀～",
    }
