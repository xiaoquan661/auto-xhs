from __future__ import annotations

from employee_operations import EmployeeOperations
from metric_temporality import event_day
from xhs.growth_notifications import normalize_growth_notifications


def metrics(rows, day="2026-09-10"):
    view = EmployeeOperations({"daily_plans": {}, "incremental_records": {},
        "worker_observations": [{"machine_id": "m", "account_slot": "a", "data": r}
                                for r in rows]}, [], lambda _: {})
    return view.day_metrics("m", "a", day)


def test_late_event_replay_uses_occurrence_date_and_counts_once():
    event = {"event_type": "note_comment", "platform_event_id": "notification1",
             "comment_id": "comment1", "occurred_at": "2026-09-09T16:02:00Z",
             "discovered_at": "2026-09-11T01:00:00Z"}
    replay = {**event, "platform_event_id": "notification2"}
    day = metrics([event, replay])["received_comments"]
    assert day["observed_new"] == 1 and day["actual"] is None
    assert day["newly_discovered"] == 0
    assert metrics([event], "2026-09-11")["received_comments"]["observed_new"] == 0


def test_snapshot_stock_and_net_change_are_separate_from_acquisitions():
    samples = [{"entity_type": "account", "entity_id": "uid", "snapshot_id": str(i),
                "captured_at": f"2026-09-{d}T12:00:00+08:00", "followers": value}
               for i, (d, value) in enumerate([("09", 10), ("10", 8)])]
    result = metrics(samples)["new_followers"]
    assert result["current_total"] == 8
    assert result["sampled_net_change"]["value"] == -2
    assert result["actual"] is None and result["observed_new"] == 0
    assert metrics(samples[1:])["new_followers"]["sampled_net_change"] is None


def test_legacy_growth_whole_page_aggregate_and_rounded_date_are_not_daily_events():
    rows = [{"event_type": "received_like", "platform_event_id": str(i),
             "occurred_at": "2026-09-10T00:00:00+08:00", "payload": {
                 "source": "notification_dom", "time_scope": "platform_display_date",
                 "display_time": display, "content": content}}
            for i, (display, content) in enumerate([
                ("今天", "沪ICP备 xxx A赞了你的评论"),
                ("今天", "A赞了你的评论 B赞了你的评论"),
                ("今天", "A收藏了你的笔记"),
                ("1天前", "A赞了你的评论"),
            ])]
    result = metrics(rows)["received_likes"]
    assert result["observed_new"] == 0
    assert result["actual"] is None


def test_conversation_turn_is_not_individual_message_count():
    event = {"event_type": "private_message", "platform_event_id": "user:4:text",
             "occurred_at": "2026-09-10T05:38:00Z", "payload": {
                 "source": "chat_inbox", "message_count": 4,
                 "peer_turn": ["yesterday", "today"], "display_time": "11:26"}}
    assert event_day(event) == ""
    assert metrics([event])["received_private_messages"]["actual"] is None


def test_full_day_coverage_zero_and_following_day_does_not_leak():
    observation = {"event_type": "collection_observation", "platform_event_id": "cycle1",
                   "occurred_at": "2026-09-10T23:59:59+08:00", "payload": {
                       "metric": "received_comments", "collection_state": "SUCCESS",
                       "coverage": "complete", "unit": "event", "undated_count": 0,
                       "period_start": "2026-09-10T00:00:00+08:00",
                       "period_end": "2026-09-11T00:00:00+08:00"}}
    assert metrics([observation])["received_comments"]["actual"] == 0
    assert metrics([observation], "2026-09-09")["received_comments"]["actual"] is None


def test_growth_identity_survives_relative_label_change():
    raw = {"event_type": "new_follower", "user_id": "u", "content": "A关注了你昨天",
           "display_time": "昨天"}
    first = normalize_growth_notifications([raw], "2026-09-10T08:00:00Z")[0]
    later = normalize_growth_notifications([{**raw, "content": "A关注了你1天前",
        "display_time": "1天前"}], "2026-09-11T08:00:00Z")[0]
    assert first["platform_event_id"] == later["platform_event_id"]
    assert event_day(later) == ""


def test_daily_facts_migration_preserves_raw_records_and_is_repeatable(tmp_path):
    from manager_database import ManagerDatabase

    db = ManagerDatabase(tmp_path)
    state = {"accounts": {"m:a": {"machine_id": "m", "account_slot": "a"}},
             "personas": {}, "results": {}, "tasks": {}}
    db.sync(state)
    snapshot = {"success": True, "exported_at": "2026-09-11T01:00:00Z", "source": "test",
                "schema": {}, "tables": {"inbound_events": [{
                    "account_slot": "a", "event_type": "note_comment", "event_id": "e",
                    "occurred_at": "2026-09-10T08:00:00Z", "payload_json": "{}"}]}}
    db.import_worker("m", snapshot)
    db.sync(state)
    db.sync(state)
    row = db.query("SELECT actual, observed_new FROM daily_metric_facts WHERE machine_id='m' "
                   "AND account_slot='a' AND business_date='2026-09-10' "
                   "AND metric='received_comments'")["rows"]
    assert row == [{"actual": None, "observed_new": 1}]
    assert db.query("SELECT count(*) n FROM worker_records WHERE collection='inbound_events'")[
        "rows"] == [{"n": 1}]
