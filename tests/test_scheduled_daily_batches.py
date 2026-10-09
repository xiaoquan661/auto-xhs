from __future__ import annotations

import copy
from datetime import datetime

import pytest
from daily_operations_plan import normalize_policy
from daily_operations_runner import DailyOperationsRunner
from manager_agent_service import ManagerAgentService
from service_errors import ServiceError

from tests.test_daily_operations_plan import manager_at, submitted


def test_entire_timetable_persists_and_failed_delivery_reuses_request(tmp_path, monkeypatch):
    manager = manager_at(tmp_path, monkeypatch)
    task = submitted(manager, legacy=False)

    def configure(row):
        spec = row["daily_material"]
        spec["budgets"].update(browse=30, like=6, comment=0, friend_request=0)
        spec["strategy"].update(
            batch_start_times=["15:00", "16:00", "17:00"],
            process_inbound_comments=False,
            action_spacing_seconds=300,
        )
        return row

    task = manager.store.update("operation_tasks", task["operation_task_id"], configure)
    calls = []

    def dispatch(request):
        calls.append(copy.deepcopy(request))
        failed = len(calls) == 2
        manager.store.put(
            "tasks",
            request["task_id"],
            {
                "task_id": request["task_id"],
                "request": request,
                "dispatch_state": "PENDING_DISPATCH" if failed else "ACKNOWLEDGED",
                "state": "PENDING_DISPATCH" if failed else "ACCEPTED",
                "created_at": "2026-09-09T14:00:00+08:00",
            },
        )
        return {"success": not failed, "status": "PENDING_DISPATCH" if failed else "TASK_ACK"}

    monkeypatch.setattr(manager, "dispatch_task", dispatch)
    monkeypatch.setattr(manager, "collect_results", lambda *a, **k: {})
    runner = DailyOperationsRunner(ManagerAgentService(manager))
    now = datetime.fromisoformat("2026-09-09T14:00:00+08:00")
    result = runner.tick(task, now)
    assert not result["success"]
    assert len(calls) == 3  # All three are delivered before the first start time.
    assert [r["parameters"]["browse_count"] for r in calls] == [10, 10, 10]
    assert [r["parameters"]["batch_expected_metrics"] for r in calls] == [
        {"browse": 10, "like": 2}
    ] * 3
    assert all(
        r["parameters"]["daily_expected_metrics"]["browse"] == 30
        and r["parameters"]["daily_expected_metrics"]["like"] == 6
        for r in calls
    )
    assert [r["parameters"]["scheduled_at"][11:16] for r in calls] == ["15:00", "16:00", "17:00"]
    assert calls[2]["parameters"]["depends_on_task_ids"] == [calls[1]["task_id"]]
    persisted = manager.store.get("operation_tasks", task["operation_task_id"])
    assert len(persisted["batch_requests"]) == 3
    # A new manager runner resumes only the delivery that failed, never rebuilds the plan.
    DailyOperationsRunner(ManagerAgentService(manager)).tick(persisted, now)
    assert len(calls) == 4
    assert calls[-1] == calls[1]


@pytest.mark.parametrize("times", [["25:00"], ["16:00", "15:00"], ["15:00", "15:00"]])
def test_batch_times_are_valid_ordered_clock_times(times):
    with pytest.raises(ServiceError):
        normalize_policy({"batch_start_times": times})


def test_predelivery_policy_uses_scheduled_execution_time(tmp_path, monkeypatch):
    from tests.test_manager_service import register_ready, service, task_request

    manager, _ = service(tmp_path)
    register_ready(manager)
    manager.store.put(
        "accounts",
        "PC001:brand-a",
        {
            "machine_id": "PC001",
            "account_slot": "brand-a",
            "operations_config": {"time_windows": [{"start": "15:00", "end": "17:00"}]},
        },
    )
    observed = []
    monkeypatch.setattr(
        "manager_service.check_operations_policy",
        lambda *args, **kwargs: observed.append(kwargs["now"]),
    )
    request = task_request()
    request["parameters"]["scheduled_at"] = "2026-09-23T15:00:00+08:00"
    manager.dispatch_task(request)
    assert observed == [datetime.fromisoformat("2026-09-23T15:00:00+08:00")]
