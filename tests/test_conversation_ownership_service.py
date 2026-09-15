from __future__ import annotations

from scripts.conversation_ownership_service import (
    OWNER_CODEX,
    RESUME_CODEX_OUTBOUND,
    ConversationOwnershipService,
)
from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase
from scripts.reply_cycle_service import ReplyCycleService


def _service(tmp_path) -> ConversationOwnershipService:
    return ConversationOwnershipService(OperationsDatabase(path=tmp_path / "operations.db"))


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
    service.track_pending_outbound(
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
