from __future__ import annotations

import copy
import json
from pathlib import Path

from application_service import ApplicationService
from business_runner import BusinessRunner
from product_store import ProductStore
from xhs.private_inbox import collect_private_messages

FIXTURE = Path(__file__).parent / "fixtures" / "private-inbox" / "received-peer-turn.json"


class HistoryPage:
    def navigate(self, _url: str) -> None:
        pass

    def wait_for_load(self) -> None:
        pass

    def wait_dom_stable(self) -> None:
        pass


def collect(monkeypatch, history: dict | None = None) -> dict:
    history = history or json.loads(FIXTURE.read_text(encoding="utf-8"))
    monkeypatch.setattr("xhs.private_inbox.read_conversation_history", lambda *_: ([], False))
    monkeypatch.setattr("xhs.private_history.collect_message_history", lambda *_: history)
    return collect_private_messages(HistoryPage(), expected_owner_uid="fixture-owner")


def test_native_history_builds_the_latest_received_peer_turn(monkeypatch) -> None:
    result = collect(monkeypatch)

    assert result["status"] == "SUCCESS"
    assert result["source"] == "chat_message_history"
    assert result["conversation_count"] == 1
    assert result["count"] == 1
    assert len(result["message_events"]) == 2
    latest = result["messages"][0]
    assert latest["message_id"] == "peer-1:message-3"
    assert latest["platform_message_id"] == "message-3"
    assert latest["content"] == "本轮收到文本"
    assert latest["peer_turn"] == ["本轮收到文本"]
    assert latest["time_scope"] == "platform"
    assert latest["message_count"] == 3
    assert [row["role"] for row in latest["context"]] == ["peer", "self", "peer"]
    assert result["conversation_snapshots"][0]["latest_role"] == "peer"


def test_native_history_keeps_outgoing_latest_turn_out_of_reply_candidates(monkeypatch) -> None:
    history = json.loads(FIXTURE.read_text(encoding="utf-8"))
    outgoing = copy.deepcopy(history["messages"][1])
    outgoing.update(store_id=4, platform_message_id="message-4", content="本轮己方回复")
    history["messages"].append(outgoing)

    result = collect(monkeypatch, history)

    assert result["count"] == 0
    assert result["conversation_count"] == 1
    assert len(result["message_events"]) == 2
    assert result["conversation_snapshots"][0]["latest_role"] == "self"
    assert result["skipped"] == [{"user_id": "peer-1", "reason": "latest_message_not_peer"}]


def test_native_peer_turn_creates_one_new_event_and_deduplicates_recollection(
    tmp_path,
    monkeypatch,
) -> None:
    collected = collect(monkeypatch)
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(lambda *_: collected),
    )
    service.get_account_status = lambda _: {
        "ready": True,
        "next_action": None,
        "identity": {"live_user_id": "fixture-owner"},
    }
    old = service.inbound_events.record(
        account_slot="alpha",
        event_type="private_message",
        platform_event_id="peer-1:1:此前收到文本",
        occurred_at="2026-10-14T07:31:28Z",
        object_type="conversation",
        object_id="peer-1",
        payload={"user_id": "peer-1", "content": "此前收到文本", "message_count": 1},
        classification="incoming_text",
    )["event"]
    service.inbound_events.mark_handled(old["event_id"])
    service.private_message_conversations.mark_codex_handled(
        account_slot="alpha",
        platform="xiaohongshu",
        user_id="peer-1",
        message_count=1,
    )

    first = service.collect_reply_inbox("alpha", channels=["private_message"])
    repeated = service.collect_reply_inbox("alpha", channels=["private_message"])

    assert first["count"] == 1
    assert repeated["count"] == 1
    assert first["events"][0]["event_id"] == repeated["events"][0]["event_id"]
    assert first["collection_results"][0]["result"]["inbound_events"]["created_count"] == 1
    assert repeated["collection_results"][0]["result"]["inbound_events"]["created_count"] == 0
    assert repeated["collection_results"][0]["result"]["inbound_events"]["duplicate_count"] == 1
    event = service.inbound_events.get(first["events"][0]["event_id"])
    assert event["payload"]["peer_turn"] == ["本轮收到文本"]
    assert event["payload"]["conversation_owner"] == "CODEX"
    assert event["payload"]["handled_through_message_count"] == 1
    assert service.inbound_events.get(old["event_id"])["handling_state"] == "HANDLED"

    service.inbound_events.mark_handled(event["event_id"])
    after_handled = service.collect_reply_inbox("alpha", channels=["private_message"])
    assert after_handled["count"] == 0
    assert after_handled["collection_results"][0]["result"]["inbound_events"]["created_count"] == 0
