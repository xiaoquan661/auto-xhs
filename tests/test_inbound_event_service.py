from __future__ import annotations

from scripts.inbound_event_service import InboundEventService
from scripts.operations_db import OperationsDatabase


def _service(tmp_path) -> InboundEventService:
    return InboundEventService(OperationsDatabase(path=tmp_path / "operations.db"))


def test_event_natural_key_deduplicates_repeated_collection(tmp_path) -> None:
    service = _service(tmp_path)
    values = {
        "account_slot": "alpha",
        "event_type": "note_comment",
        "platform_event_id": "comment-1",
        "occurred_at": "2026-08-19T10:00:00+00:00",
        "object_type": "note",
        "object_id": "note-1",
        "actor_user_id": "user-1",
        "payload": {"content": "请问怎么报名？"},
    }

    first = service.record(**values)
    duplicate = service.record(**values)

    assert first["created"] is True
    assert duplicate["created"] is False
    assert duplicate["event"]["event_id"] == first["event"]["event_id"]
    assert duplicate["event"]["payload"]["content"] == "请问怎么报名？"
    assert len(service.list(account_slot="alpha")) == 1


def test_event_can_be_linked_to_passive_task_and_marked_handled(tmp_path) -> None:
    service = _service(tmp_path)
    event = service.record(
        account_slot="alpha",
        event_type="note_comment",
        platform_event_id="comment-2",
        occurred_at="2026-08-19T10:01:00+00:00",
    )["event"]

    linked = service.attach_task(event["event_id"], "task-1")
    handled = service.mark_handled(event["event_id"])

    assert linked["handling_state"] == "TASK_CREATED"
    assert linked["created_task_id"] == "task-1"
    assert handled["handling_state"] == "HANDLED"
    assert handled["created_task_id"] == "task-1"
