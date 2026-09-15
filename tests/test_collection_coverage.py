from __future__ import annotations

from collection_coverage import batch_summary
from employee_operations import EmployeeOperations
from metric_temporality import display_date, event_day


def metrics(rows, day="2026-09-10"):
    view = EmployeeOperations(
        {
            "daily_plans": {},
            "personas": {},
            "incremental_records": {},
            "worker_observations": [
                {"machine_id": "m", "account_slot": "a", "data": r} for r in rows
            ],
        },
        [],
        lambda _: {},
    )
    return view.day_metrics("m", "a", day)


def observation(key, start, end, *, coverage="complete", unit="event", metric="received_comments"):
    return {
        "event_type": "collection_observation",
        "platform_event_id": key,
        "occurred_at": "2026-09-11T12:00:00+08:00",
        "payload": {
            "metric": metric,
            "collection_state": "SUCCESS",
            "coverage": coverage,
            "unit": unit,
            "undated_count": 0,
            "period_start": start,
            "period_end": end,
        },
    }


def test_next_day_backfill_and_overlapping_batches_close_yesterday():
    rows = [
        observation("a", "2026-09-10T00:00:00+08:00", "2026-09-10T13:00:00+08:00"),
        observation("b", "2026-09-10T12:00:00+08:00", "2026-09-11T03:00:00+08:00"),
    ]
    assert batch_summary(rows, "received_comments", "2026-09-10")["complete"]
    assert metrics(rows)["received_comments"]["actual"] == 0
    assert metrics(rows, "2026-09-09")["received_comments"]["actual"] is None
    rows.append(
        observation(
            "failed", "2026-09-10T00:00:00+08:00", "2026-09-11T00:00:00+08:00", coverage="partial"
        )
    )
    assert metrics(rows)["received_comments"]["actual"] == 0


def test_gap_remains_and_conversation_units_do_not_confirm_messages():
    row = observation("a", "2026-09-10T01:00:00+08:00", "2026-09-11T00:00:00+08:00")
    result = batch_summary([row], "received_comments", "2026-09-10")
    assert not result["complete"] and result["missing_intervals"][0]["end"].endswith(
        "01:00:00+08:00"
    )
    row = observation(
        "dm",
        "2026-09-10T00:00:00+08:00",
        "2026-09-11T00:00:00+08:00",
        unit="conversation_turn",
        metric="received_private_messages",
    )
    assert not batch_summary([row], "received_private_messages", "2026-09-10")["complete"]


def test_private_messages_count_individually_across_reply_turns_and_replay():
    from xhs.private_inbox import message_observations

    raw = [
        {
            "role": "peer",
            "content": "same",
            "platform_message_id": "1",
            "occurred_at": "2026-09-10T08:00:00Z",
        },
        {"role": "self", "content": "reply"},
        {
            "role": "peer",
            "content": "same",
            "platform_message_id": "2",
            "occurred_at": "2026-09-10T09:00:00Z",
        },
    ]
    events, missing = message_observations(raw, "user", "2026-09-11T01:00:00Z")
    assert len(events) == 2 and missing == 0
    assert metrics(events + events)["received_private_messages"]["observed_new"] == 2
    assert (
        message_observations([{"role": "peer", "content": "unknown"}], "u", "2026-09-11T01:00:00Z")[
            1
        ]
        == 1
    )


def test_precise_time_survives_later_undated_observation(tmp_path):
    from inbound_event_service import InboundEventService
    from operations_db import OperationsDatabase

    service = InboundEventService(OperationsDatabase(tmp_path / "operations.db"))
    kwargs = dict(
        account_slot="a",
        event_type="new_follower",
        platform_event_id="event",
        occurred_at="2026-09-11T01:00:00Z",
    )
    first = service.record(**kwargs, payload={"time_scope": "first_observed"})
    second = service.record(
        **{**kwargs, "occurred_at": "2026-09-10T08:00:00Z"}, payload={"time_scope": "platform"}
    )
    third = service.record(**kwargs, payload={"time_scope": "relative_approximate"})
    assert first["created"] and not second["created"] and not third["created"]
    assert event_day(third["event"]) == "2026-09-10"
    assert first["event"]["discovered_at"] == third["event"]["discovered_at"]


def test_relative_date_only_resolves_within_one_day():
    assert display_date("2小时前", "2026-09-11T12:00:00+08:00").startswith("2026-09-11")
    assert display_date("2小时前", "2026-09-11T02:30:00+08:00") == ""
    assert display_date("1天前", "2026-09-11T12:00:00+08:00") == ""
    assert display_date("09-10", "2026-09-11T12:00:00+08:00").startswith("2026-09-10")


def test_notification_paging_merges_pages_and_limit_remains_partial():
    from xhs.notification_history import read_notification_history

    class Page:
        index = 0

        def wait_dom_stable(self):
            pass

        def evaluate(self, script):
            if "markers" in script:
                return self.index == 1
            self.index = 1

    page = Page()
    rows, complete, _ = read_notification_history(
        page, lambda: [{"notification_id": str(page.index)}], max_items=5
    )
    assert complete and len(rows) == 2
    page = Page()
    rows, complete, reason = read_notification_history(
        page, lambda: [{"notification_id": str(page.index)}], max_items=2
    )
    assert not complete and len(rows) == 2 and "上限" in reason


def test_history_collection_retains_latest_reply_context(monkeypatch):
    import importlib.util
    from pathlib import Path

    from xhs import private_inbox

    spec = importlib.util.spec_from_file_location(
        "private_fixture", Path(__file__).with_name("test_private_inbox.py")
    )
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)

    def history(page, state):
        return {**state, "messages": [{"role": "peer", "content": "old"}], "history_complete": True}

    monkeypatch.setattr(private_inbox, "read_message_history", history)
    result = private_inbox.collect_private_messages(fixture.FakePage())
    assert result["messages"][0]["content"] == "你好"
    assert result["conversation_snapshots"][1]["latest_role"] == "self"


def test_zero_event_day_exists_in_summary_after_next_day_collection():
    row = observation("empty", "2026-09-10T00:00:00+08:00", "2026-09-11T00:00:00+08:00")
    view = EmployeeOperations(
        {
            "daily_plans": {},
            "personas": {},
            "incremental_records": {},
            "worker_observations": [{"machine_id": "m", "account_slot": "a", "data": row}],
        },
        [],
        lambda _: {},
    )
    summary = view.summary([{"machine_id": "m", "account_slot": "a"}])
    yesterday = next(r for r in summary["daily_metrics"] if r["date"] == "2026-09-10")
    assert yesterday["metrics"]["received_comments"]["actual"] == 0


def test_complete_event_index_confirms_zero_without_legacy_dom_duplicates():
    batch = observation("api", "2026-09-10T00:00:00+08:00", "2026-09-11T12:00:00+08:00")
    batch["payload"]["event_index"] = [{"id": "old", "date": "2026-09-09"}]
    legacy = {
        "event_type": "note_comment",
        "platform_event_id": "legacy",
        "occurred_at": "2026-09-10T12:00:00+08:00",
    }
    assert metrics([batch, legacy])["received_comments"]["actual"] == 0


def test_growth_api_counts_likes_separately_from_favorites_and_checks_pages():
    from xhs.growth_notifications import parse_growth_api

    first = {
        "url": "https://edith.xiaohongshu.com/api/sns/web/v1/you/likes?cursor=",
        "payload": {
            "data": {
                "has_more": True,
                "strCursor": "cursor-1",
                "message_list": [
                    {"id": "favorite", "time": 1789037039, "type": "faved/item"},
                    {
                        "id": "like",
                        "time": 1788951104,
                        "type": "liked/comment",
                        "user_info": {"userid": "actor"},
                    },
                ],
            }
        },
    }
    last = {
        "url": first["url"] + "cursor-1",
        "payload": {"data": {"has_more": False, "message_list": []}},
    }
    key = "/api/sns/web/v1/you/likes"
    captured = {key: {"pages": [first, last]}}
    rows, complete = parse_growth_api(captured, "received_likes", "2026-09-11T12:00:00Z")
    assert complete and [r["platform_event_id"] for r in rows] == ["like"]
    last["url"] += "-missing-page"
    assert not parse_growth_api(captured, "received_likes", "2026-09-11T12:00:00Z")[1]
