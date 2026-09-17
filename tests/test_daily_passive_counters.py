from __future__ import annotations

from datetime import datetime

from collection_coverage import batch_summary

from tests.test_collection_coverage import metrics, observation


def freeze_today(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 11, 17, tzinfo=tz)

    monkeypatch.setattr("collection_coverage.datetime", Clock)
    monkeypatch.setattr("employee_operations.datetime", Clock)


def test_today_complete_through_sample_clears_superseded_failure(monkeypatch):
    freeze_today(monkeypatch)
    old = observation("old", "2026-09-11T00:00:00+08:00",
                      "2026-09-11T13:00:00+08:00", coverage="partial")
    old["payload"]["reason"] = "旧采集缺少日期"
    good = observation("good", "2026-09-10T00:00:00+08:00",
                       "2026-09-11T14:30:00+08:00")
    good["payload"]["event_index"] = [{"id": "today", "date": "2026-09-11"},
                                       {"id": "yesterday", "date": "2026-09-10"}]
    result = metrics([old, good], "2026-09-11")["received_comments"]
    assert result["actual"] == result["daily_new"] == 1
    assert result["collection_progress"]["missing_intervals"] == []
    assert result["collection_progress"]["reasons"] == []
    assert result["collection_reason"] == ""
    assert not result["day_finalized"]
    assert "14:30" in result["as_of"]
    assert metrics([old, good], "2026-09-10")["received_comments"]["actual"] == 1


def test_new_day_without_collection_displays_unknown(monkeypatch):
    freeze_today(monkeypatch)
    yesterday = observation("yesterday", "2026-09-10T00:00:00+08:00",
                            "2026-09-11T00:00:00+08:00")
    yesterday["payload"]["event_index"] = [{"id": "old", "date": "2026-09-10"}]
    result = metrics([yesterday], "2026-09-11")
    for key in ("new_followers", "received_likes", "received_comments",
                "received_private_messages"):
        assert result[key]["daily_new"] == 0
        assert result[key]["actual_display"] == "—"
        assert result[key]["passive_view"]["status_code"] == "not_collected"
        assert result[key]["actual"] is None
        assert result[key]["status"] == "待首次采集"
    assert metrics([yesterday], "2026-09-10")["received_comments"]["actual"] == 1


def test_actual_missing_interval_remains_visible(monkeypatch):
    freeze_today(monkeypatch)
    good = observation("good", "2026-09-11T00:00:00+08:00",
                       "2026-09-11T12:00:00+08:00")
    failed = observation("failed", "2026-09-11T12:00:00+08:00",
                         "2026-09-11T14:00:00+08:00", coverage="partial")
    failed["payload"]["reason"] = "本轮分页失败"
    result = metrics([good, failed], "2026-09-11")["received_comments"]
    assert result["actual"] is None
    assert result["status"] == "采集不完整"
    assert result["collection_reason"] == "本轮分页失败"
    assert batch_summary([good, failed], "received_comments", "2026-09-11")[
        "missing_intervals"
    ] == [{"start": "2026-09-11T12:00:00+08:00", "end": "2026-09-11T14:00:00+08:00"}]


def test_partial_events_show_lower_bound_and_exact_missing_interval(monkeypatch):
    freeze_today(monkeypatch)
    batch = observation("half", "2026-09-10T00:00:00+08:00",
                        "2026-09-10T12:00:00+08:00")
    event = {"event_type": "note_comment", "event_id": "comment",
             "occurred_at": "2026-09-10T09:00:00+08:00", "handling_state": "NEW"}
    result = metrics([batch, event])["received_comments"]["passive_view"]
    assert result["count_display"] == "至少 1"
    assert result["pending_count"] == 1
    assert result["status_code"] == "partial"
    assert "区间" in result["reason"]
    assert result["missing_intervals"][0]["start"].endswith("12:00:00+08:00")


def test_business_receipt_coverage_is_recovered_without_inventing_intervals():
    from employee_operations import EmployeeOperations

    state = {"daily_plans": {}, "personas": {}, "incremental_records": {}}
    batch = observation("full", "2026-09-10T00:00:00+08:00",
                        "2026-09-11T00:00:00+08:00")["payload"]
    result = {"task_id": "collect", "machine_id": "m", "account_slot": "a",
              "capability": "collect-note-comments", "state": "SUCCESS",
              "recorded_at": "2026-09-11T03:00:00+08:00",
              "business_result": {"collection_batches": [batch]}}
    view = EmployeeOperations(state, [result], lambda _: {})
    value = view.day_metrics("m", "a", "2026-09-10")["received_comments"]
    assert value["actual"] == 0
    assert value["passive_view"]["status_code"] == "complete"
    result["business_result"] = {"count": 0}
    view = EmployeeOperations(state, [result], lambda _: {})
    assert view.day_metrics("m", "a", "2026-09-10")["received_comments"]["actual"] is None
    result.update(state="FAILED", recorded_at="2026-09-10T10:00:00+08:00",
                  result_summary="页面读取失败")
    view = EmployeeOperations(state, [result], lambda _: {})
    value = view.day_metrics("m", "a", "2026-09-10")["received_comments"]["passive_view"]
    assert value["status_code"] == "failed"
    assert value["count"] is None
    assert value["reason"] == "页面读取失败"


def test_reply_failure_is_distinct_from_inbox_collection_failure():
    from employee_operations import EmployeeOperations

    state = {"daily_plans": {}, "personas": {}, "incremental_records": {}}
    result = {"task_id": "poll", "machine_id": "m", "account_slot": "a",
              "capability": "reply-inbox", "state": "FAILED",
              "recorded_at": "2026-09-10T10:00:00+08:00",
              "business_result": {"collection_success": True}}
    view = EmployeeOperations(state, [result], lambda _: {})
    value = view.day_metrics("m", "a", "2026-09-10")["received_comments"]["passive_view"]
    assert value["status_code"] == "not_collected"
    result["business_result"]["collection_success"] = False
    view = EmployeeOperations(state, [result], lambda _: {})
    value = view.day_metrics("m", "a", "2026-09-10")["received_comments"]["passive_view"]
    assert value["status_code"] == "failed"
