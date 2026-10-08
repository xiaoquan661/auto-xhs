from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import account_manager
import daily_passive_collection
import direct_notification_collection
import product_store
import pytest
from manager_worker import dispatch_request

from tests.test_manager_worker import make_service


@pytest.mark.parametrize("eligibility_status", ["READY", "DEFERRED"])
def test_interactive_notifications_collect_two_channels_and_return_events(
    tmp_path: Path,
    monkeypatch,
    eligibility_status: str,
) -> None:
    worker = make_service(tmp_path / "worker")
    monkeypatch.setattr(
        account_manager,
        "list_accounts",
        lambda: [SimpleNamespace(name="alpha", platform="xiaohongshu")],
    )
    monkeypatch.setattr(daily_passive_collection, "eligibility", lambda *args: eligibility_status)
    monkeypatch.setattr(
        direct_notification_collection, "eligibility", lambda *args: eligibility_status
    )
    monkeypatch.setattr(
        product_store, "ProductStore", lambda: SimpleNamespace(snapshot=lambda: {"settings": {}})
    )
    calls = []

    def collect(slot: str, command: list[str]) -> dict:
        calls.append((slot, command))
        if command[0] == "collect-note-comments":
            return {
                "task": {"state": "SUCCESS"},
                "result": {
                    "inbound_events": {
                        "observed_count": 1,
                        "created_events": [
                            {"event_type": "note_comment", "platform_event_id": "comment-1"}
                        ],
                    },
                },
            }
        return {
            "task": {"state": "SUCCESS"},
            "result": {
                "growth_notifications": {"events": [{"event_type": "new_follower"}]},
                "snapshots": {
                    "events": [{"event_type": "new_follower", "platform_event_id": "follow-1"}]
                },
            },
        }

    monkeypatch.setattr(daily_passive_collection.DailyCollector, "collect", collect)
    response = dispatch_request(
        worker,
        {
            "action": "collect-notifications-now",
            "payload": {"account_slot": "alpha"},
        },
    )

    assert response["status"] == "SUCCESS"
    assert response["result"]["source_type"] == "manager_direct"
    assert [row["event_type"] for row in response["result"]["incremental_data"]] == [
        "note_comment",
        "new_follower",
    ]
    assert [row["observed_count"] for row in response["result"]["item_results"]] == [1, 1]
    assert len(calls) == 2
    assert calls[1][1][-2:] == ["--notification-kind", "new_followers"]
