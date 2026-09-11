from __future__ import annotations

from scripts.employee_operations import EmployeeOperations
from scripts.manager_service import ManagerService
from scripts.manager_worker_service import ManagerWorkerService
from scripts.polling_receipt import cycle_observations


def test_polling_outbox_to_daily_metrics() -> None:
    cycle = {"cycle_id": "round1", "account_slot": "a", "started_at": "2026-09-10T08:00:00Z"}
    collections = [{"task": {"task_id": "collect", "capability": "collect-note-comments",
                             "state": "SUCCESS"},
                    "result": {"count": 0, "inbound_events": {"created_events": []}}}]
    replies = [{"account_slot": "a", "reply_task_id": "r1", "channel": "COMMENT_REPLY",
                "send_state": "CONFIRMED", "sent_at": cycle["started_at"]}]
    observations = cycle_observations(cycle, collections, replies)
    normalized = ManagerWorkerService._normalize_outcome({
        "success": True, "incremental_data": observations,
    })
    receipt = {**normalized, "machine_id": "PC057", "account_slot": "a", "task_id": "poll",
               "recorded_at": cycle["started_at"], "capability": "reply-cycle"}
    state = {"incremental_records": {}, "tasks": {}, "daily_plans": {"p": {
        "plan_date": "2026-09-10", "accounts": [{"machine_id": "PC057", "account_slot": "a",
        "participating": True, "metrics": {"reply": 1, "received_comments": 99}}],
    }}}
    metrics = EmployeeOperations(state, [receipt, receipt], ManagerService._result_metric_counts)
    day = metrics.day_metrics("PC057", "a", "2026-09-10")
    assert day["reply"]["target"] == 1 and day["reply"]["actual"] == 1
    assert day["received_comments"]["target"] is None
    assert day["received_comments"]["actual"] is None
    assert day["received_comments"]["collection"]["observed_count"] == 0
    assert day["reply"]["execution_source"] == "worker_polling"
    assert metrics.day_metrics("PC052", "a", "2026-09-10")["reply"]["actual"] is None


def test_completed_target_keeps_failure_history_without_blocking() -> None:
    state = {"incremental_records": {}, "tasks": {}, "daily_plans": {"p": {
        "plan_date": "2026-09-10", "accounts": [{"machine_id": "m", "account_slot": "a",
        "participating": True, "metrics": {"reply": 1}}],
    }}}
    base = {"machine_id": "m", "account_slot": "a", "recorded_at": "2026-09-10T08:00:00Z",
            "capability": "reply-comment"}
    records = [{**base, "state": "BLOCKED", "actual_count": 0},
               {**base, "state": "SUCCESS", "actual_count": 1}]
    view = EmployeeOperations(state, records, ManagerService._result_metric_counts)
    assert view.day_metrics("m", "a", "2026-09-10")["reply"]["status"] == "完成"
