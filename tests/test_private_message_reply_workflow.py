from __future__ import annotations

from scripts.application_service import ApplicationService
from scripts.business_runner import BusinessRunner
from scripts.operations_db import OperationsDatabase
from scripts.product_store import ProductStore


def test_reviewed_private_message_draft_sends_once_and_marks_event_handled(tmp_path) -> None:
    calls: list[tuple[str, dict]] = []

    def executor(_account: str, capability: str, parameters: dict) -> dict:
        calls.append((capability, parameters))
        return {
            "success": True,
            "message": "私信发送成功",
            "readback": {
                "user_id": parameters["user_id"],
                "content": parameters["content"],
                "outgoing_message_present": True,
            },
        }

    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
        business_runner=BusinessRunner(executor),
    )
    service.get_account_status = lambda _account: {
        "ready": True,
        "next_action": None,
        "identity": {"live_user_id": "owner-1"},
    }
    event = service.inbound_events.record(
        account_slot="alpha",
        event_type="private_message",
        platform_event_id="user-1:1:你好",
        occurred_at="2026-08-21T10:00:00+00:00",
        object_type="conversation",
        object_id="user-1",
        actor_user_id="user-1",
        payload={"user_id": "user-1", "nickname": "小红", "content": "你好"},
    )["event"]
    prepared = service.create_passive_reply_draft(
        event["event_id"],
        verified_uid="owner-1",
        content="你好呀～",
    )

    assert calls == []
    assert prepared["task"]["state"] == "WAITING_APPROVAL"
    assert prepared["task"]["operation"] == "reviewed_reply"
    confirmed = service.confirm_draft(prepared["draft"]["draft_id"])
    result = service.execute_draft(
        prepared["draft"]["draft_id"],
        approval_id=confirmed["approval"]["approval_id"],
    )

    assert result["task"]["state"] == "SUCCESS"
    assert calls[0][0] == "send-private-messages"
    assert calls[0][1]["user_id"] == "user-1"
    assert calls[0][1]["content"] == "你好呀～"
    assert service.inbound_events.get(event["event_id"])["handling_state"] == "HANDLED"
