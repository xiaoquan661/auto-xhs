from __future__ import annotations

from metric_temporality import event_day
from xhs.growth_notifications import normalize_growth_notifications


def test_growth_ignores_page_aggregate_and_favorite():
    rows = [{"event_type": "received_like", "user_id": "u", "display_time": "今天",
             "content": text} for text in [
                 "沪ICP备 A赞了你的评论", "A赞了你的评论 B赞了你的评论", "A收藏了你的笔记"]]
    assert normalize_growth_notifications(rows, "2026-09-10T08:00:00Z") == []


def test_growth_relative_date_and_identity():
    row = {"event_type": "new_follower", "user_id": "u", "content": "A关注了你昨天",
           "display_time": "昨天"}
    first = normalize_growth_notifications([row], "2026-09-10T08:00:00Z")[0]
    second = normalize_growth_notifications([{**row, "content": "A关注了你1天前",
        "display_time": "1天前"}], "2026-09-11T08:00:00Z")[0]
    assert first["platform_event_id"] == second["platform_event_id"]
    assert event_day(first) == "2026-09-09"
    assert event_day(second) == ""


def test_growth_accepts_current_follower_notification_wording():
    rows = [{"event_type": "new_follower", "user_id": "u", "display_time": "2天前",
             "content": "读者开始关注你了2天前"}]

    events = normalize_growth_notifications(rows, "2026-09-29T06:00:00Z")

    assert len(events) == 1
    assert events[0]["event_type"] == "new_follower"


def test_conversation_has_observation_time_separate_from_event_time():
    row = {"event_type": "private_message", "occurred_at": "2026-09-10T08:00:00Z",
           "payload": {"source": "chat_inbox", "message_count": 4}}
    assert event_day(row) == ""


def test_growth_collection_outbox_counts_events_instead_of_notes():
    from polling_receipt import cycle_observations

    rows = cycle_observations({"cycle_id": "c", "account_slot": "a", "started_at": "now"},
        [{"task": {"capability": "collect-operations-metrics", "state": "SUCCESS"},
          "result": {"count": 50, "growth_notifications": {
              "coverage": "visible_notifications", "events": [
                  {"event_type": "new_follower"}, {"event_type": "received_like"}]}}}], [])
    summaries = [r["payload"] for r in rows if r.get("event_type") == "collection_observation"]
    assert [r["observed_count"] for r in summaries] == [1, 1]
    assert all(r["unit"] == "event" and r["period_start"] is None for r in summaries)
