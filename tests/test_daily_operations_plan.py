from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

import pytest
from daily_operations_plan import generate_account, material, normalize_policy, window
from daily_operations_runner import DailyOperationsRunner
from employee_operations import EmployeeOperations
from manager_agent_service import ManagerAgentService
from manager_service import ManagerService
from manager_store import ManagerStore
from service_errors import ServiceError
from web_server import _dispatch_mutation


def account() -> dict:
    return {
        "machine_id": "PC001",
        "account_slot": "alpha",
        "account_key": "PC001:alpha",
        "health_state": "READY",
        "current_uid": "uid-1",
        "platform": "rednote",
        "stage": "稳定运营",
        "pressure": "低压",
        "planning_evidence": {
            "comment_range": "3–6",
            "lifetime_posts": 2,
            "posts_scope": "profile_total",
            "last_post_date": "2026-09-06",
        },
    }


def manager_at(root: Path, monkeypatch: pytest.MonkeyPatch) -> ManagerService:
    manager = ManagerService(store=ManagerStore(root))
    manager.store.put("machines", "PC001", {"machine_id": "PC001"})
    manager.store.put("accounts", "PC001:alpha", account())
    monkeypatch.setattr(
        manager, "list_accounts", lambda: {"accounts": manager.store.list("accounts")}
    )
    manager.save_persona(
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "display_name": "阅读者",
            "content_topics": ["读书"],
        }
    )
    return manager


def request() -> dict:
    return {
        "plan_date": "2026-09-09",
        "accounts": [
            {
                "machine_id": "PC001",
                "account_slot": "alpha",
                "participating": True,
                "metrics": {
                    "browse": 2,
                    "like": 1,
                    "comment": 1,
                    "publish": 0,
                    "reply": 0,
                    "friend_request": 1,
                },
            }
        ],
    }


def submitted(manager: ManagerService) -> dict:
    manager.save_daily_plan(request())
    result = manager.submit_daily_plan({"plan_date": "2026-09-09", "version": 1})
    return manager.get_operation_task(result["submissions"][0]["operation_task_id"])[
        "operation_task"
    ]


def test_smart_generation_is_stable_and_uses_evidence() -> None:
    source = account()
    row = generate_account("2026-09-09", source)
    assert row == generate_account("2026-09-09", source)
    assert 3 <= row["metrics"]["comment"] <= 6
    assert row["metrics"]["publish"] == 1
    source["planning_evidence"]["posts_scope"] = "profile_sample"
    assert generate_account("2026-09-09", source)["metrics"]["publish"] == 0
    source["health_state"] = "BLOCKED"
    row = generate_account("2026-09-09", source)
    assert not row["participating"] and set(row["metrics"].values()) == {0}


def test_stage_pressure_and_cross_day_window() -> None:
    source = account()
    row = generate_account("2026-09-09", source, {"stage": "新机第 1 天"})
    assert 1 <= row["metrics"]["comment"] <= 3
    row = generate_account("2026-09-09", source, {"stage": "新机第 2–3 天"})
    assert row["metrics"]["publish"] == 0
    policy = normalize_policy({"start_time": "23:00", "end_time": "02:00"})
    assert window("2026-09-09", policy)["end_at"] == "2026-09-10T02:00:00+08:00"
    with pytest.raises(ServiceError):
        normalize_policy({"action_spacing_seconds": -1})


def test_generate_preview_submit_and_immutable_versions(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    generated = _dispatch_mutation(
        None, "POST", "/api/v1/manager/daily-plans/generate", request(), manager_service=manager
    )
    assert generated["plan"]["status"] == "DRAFT"
    assert manager.store.list("daily_plans") == []
    task = submitted(manager)
    spec = task["daily_material"]
    assert spec["identity"]["platform"] == "rednote"
    assert spec["persona_snapshot"]["display_name"] == "阅读者"
    assert spec["hard_targets"]["comment"] == 1
    assert "friend_request" not in spec["hard_targets"]
    assert manager.store.list("tasks") == []
    again = manager.submit_daily_plan({"plan_date": "2026-09-09", "version": 1})
    assert again["status"] == "IDEMPOTENT"
    changed = request()
    changed["accounts"][0]["metrics"]["comment"] = 10
    saved = manager.save_daily_plan(changed)["plan"]
    assert saved["history"][0]["accounts"][0]["metrics"]["comment"] == 1
    with pytest.raises(ServiceError, match="原任务"):
        manager.submit_daily_plan({"plan_date": "2026-09-09", "version": 2})
    assert manager.store.list("operation_tasks")[0]["daily_material"] == spec


def test_preview_blocks_health_persona_and_publish_limits(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    manager.save_daily_plan(request())
    manager.store.update("accounts", "PC001:alpha", lambda row: {**row, "health_state": "BLOCKED"})
    with pytest.raises(ServiceError, match="READY"):
        manager.submit_daily_plan({"plan_date": "2026-09-09", "version": 1})
    assert manager.store.list("operation_tasks") == []
    with pytest.raises(ServiceError, match="版本"):
        manager.preview_daily_plan({"plan_date": "2026-09-09", "version": 0})
    row = request()["accounts"][0]
    row["metrics"]["publish"] = 2
    result = material({"plan_date": "2026-09-09", "version": 1}, row, account(), None)
    assert result["blockers"] == ["发帖计划超过账号每日上限"]
    assert result["persona_snapshot"]["display_name"] == "友好评论者"
    assert result["strategy"]["publish_confirmation_required"] is False


def test_runner_waits_dispatches_and_resumes_from_receipts(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    task = submitted(manager)
    agent = ManagerAgentService(manager)
    runner = DailyOperationsRunner(agent)
    now = datetime.fromisoformat("2026-09-09T10:00:00+08:00")
    calls = []

    def dispatch(payload: dict) -> dict:
        calls.append(copy.deepcopy(payload))
        return manager.store.put(
            "tasks",
            payload["task_id"],
            {
                "task_id": payload["task_id"],
                "request": payload,
                "business_state": "RUNNING",
                "updated_at": now.isoformat(),
                "created_at": now.isoformat(),
            },
        )

    monkeypatch.setattr(manager, "dispatch_task", dispatch)
    monkeypatch.setattr(manager, "collect_results", lambda *args, **kwargs: {})
    assert runner.tick(task, now.replace(hour=8))["status"] == "WAITING_WINDOW"
    task = manager.get_operation_task(task["operation_task_id"])["operation_task"]
    assert runner.tick(task, now)["status"] == "DISPATCHED"
    assert calls[0]["capability"] == "collect-note-comments"
    assert calls[0]["parameters"]["daily_plan"]["plan_version"] == 1
    assert calls[0]["platform"] == "rednote"
    assert runner.tick(task, now)["status"] == "WAITING_RECEIPT"
    payload = calls[0]
    manager.store.update(
        "tasks", payload["task_id"], lambda row: {**row, "business_state": "SUCCESS"}
    )
    manager.store.put(
        "results",
        "receipt-1",
        {
            "record_id": "receipt-1",
            "task_id": payload["task_id"],
            "machine_id": "PC001",
            "account_slot": "alpha",
            "task_request": payload,
            "result_revision": 1,
            "operation_task_id": task["operation_task_id"],
            "capability": "collect-note-comments",
            "state": "SUCCESS",
            "recorded_at": now.isoformat(),
        },
    )
    assert runner.tick(task, now)["status"] == "WAITING_SPACING"
    assert runner.tick(task, now.replace(minute=6))["status"] == "DISPATCHED"
    assert calls[-1]["capability"] == "collect-operations-metrics"
    assert calls[-1]["parameters"]["excluded_user_ids"] == ["uid-1"]
    assert len(calls) == 2


def test_unknown_budget_is_reserved_while_other_metrics_continue() -> None:
    from collections import Counter

    spec = material(
        {"plan_date": "2026-09-09", "version": 1},
        request()["accounts"][0],
        account(),
        {"display_name": "读者"},
    )
    spec["strategy"]["process_inbound_comments"] = False
    remaining = {**spec["budgets"], "browse": 0, "like": 1}
    collected = [{"request": {"capability": "collect-operations-metrics"}}]
    action = DailyOperationsRunner.next_action(
        spec, remaining, Counter(like=1), Counter(), collected, set()
    )
    assert action["capability"] == "random-comment"
    remaining["comment"] = 0
    assert (
        DailyOperationsRunner.next_action(
            spec, remaining, Counter(like=1), Counter(), collected, set()
        )
        is None
    )


def test_report_credits_each_metric_and_uses_plan_date(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    day_plan = request()
    day_plan["accounts"][0]["metrics"].update(browse=0, like=2, comment=2)
    manager.save_daily_plan(day_plan)
    rows = [
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "capability": "like-feed",
            "state": "SUCCESS",
            "actual_count": 4,
            "completed_at": "2026-09-10T00:05:00+08:00",
            "task_request": {"parameters": {"daily_plan": {"plan_date": "2026-09-09"}}},
        }
    ]
    view = EmployeeOperations(manager.store.snapshot(), rows, manager._result_metric_counts)
    assert view.result_date(rows[0]) == "2026-09-09"
    report = view.report("2026-09-09")["rows"][0]
    assert report["credited"] == 2
    assert report["remaining"]["comment"] == 2
    assert report["completion_rate"] is None  # Missing comment receipts remain explicit.


def test_prepare_and_submit_share_one_queue_and_append_inputs(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    data = request()
    data["accounts"][0]["metrics"].update(publish=1, reply=1)
    manager.save_daily_plan(data)
    initial = manager.prepare_daily_plan(data["plan_date"], {})
    identifier = initial["operation_tasks"][0]["operation_task_id"]
    supplied = {
        "publish": [{"title": "阅读", "content": "读书笔记", "assets": ["C:/post.jpg"]}],
        "reply": [{"content": "谢谢分享", "feed_id": "n1", "comment_id": "c1"}],
    }
    result = manager.submit_daily_plan(
        {"plan_date": data["plan_date"], "version": 1, "action_inputs": {"PC001:alpha": supplied}}
    )
    assert result["submissions"][0]["operation_task_id"] == identifier
    assert len(manager.store.list("operation_tasks")) == len(manager.store.list("goals")) == 1
    task = manager.get_operation_task(identifier)["operation_task"]
    from collections import Counter

    runner = DailyOperationsRunner(ManagerAgentService(manager))
    action, errors = runner.supplied_action(task, task["daily_material"]["budgets"], Counter(), [])
    assert not errors and action["capability"] == "fill-publish"
    assert action["parameters"]["preview"]["title"] == "阅读"
    linked = [
        {"request": {"parameters": {"daily_input_metric": "publish", "daily_input_index": 0}}}
    ]
    action, errors = runner.supplied_action(
        task, task["daily_material"]["budgets"], Counter(), linked
    )
    assert action["capability"] == "reply-comment"
    with pytest.raises(ServiceError, match="保持不变"):
        manager.supply_daily_inputs(identifier, {"action_inputs": {"reply": []}})
    with pytest.raises(ServiceError, match="暖互动"):
        manager.supply_daily_inputs(
            identifier, {"action_inputs": {"friend_request": [{"user_id": "cold"}]}}
        )


def test_daily_window_and_identity_are_checked_at_execution() -> None:
    from operations_policy import check_daily_context

    spec = material(
        {"plan_date": "2026-09-09", "version": 1},
        request()["accounts"][0],
        account(),
        {"display_name": "读者"},
    )
    now = datetime.fromisoformat("2026-09-09T10:00:00+08:00")
    check_daily_context({"daily_plan": spec}, account="alpha", current_uid="uid-1", now=now)
    with pytest.raises(ServiceError, match="身份"):
        check_daily_context({"daily_plan": spec}, account="alpha", current_uid="uid-2", now=now)
    with pytest.raises(ServiceError, match="窗口"):
        check_daily_context({"daily_plan": spec}, account="alpha", now=now.replace(hour=23))


def test_submitted_report_keeps_original_targets_and_caps_each_metric(
    tmp_path: Path, monkeypatch
) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    submitted(manager)
    data = request()
    data["accounts"][0]["metrics"]["comment"] = 100
    manager.save_daily_plan(data)
    rows = [
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "capability": "post-comment",
            "state": "SUCCESS",
            "actual_count": 0,
            "recorded_at": "2026-09-09T12:00:00+08:00",
        },
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "capability": "home-engagement",
            "state": "SUCCESS",
            "recorded_at": "2026-09-09T12:00:00+08:00",
            "item_results_status": "observed",
            "item_results": [
                {"browse": {"status": "success"}, "like": {"success": True}} for _ in range(10)
            ],
        },
    ]
    report = EmployeeOperations(
        manager.store.snapshot(), rows, manager._result_metric_counts
    ).report("2026-09-09")
    assert report["plan_version"] == 1
    assert report["rows"][0]["completion_rate"] == 0.75
    assert report["rows"][0]["remaining"]["comment"] == 1


def test_unknown_reservation_credits_confirmed_part_without_replaying(
    tmp_path: Path, monkeypatch
) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    task = submitted(manager)
    manager.store.put(
        "tasks",
        "uncertain",
        {
            "task_id": "uncertain",
            "created_at": "2026-09-09T09:00:00+08:00",
            "business_state": "RESULT_UNKNOWN",
            "request": {
                "operation_task_id": task["operation_task_id"],
                "parameters": {"daily_expected_metrics": {"browse": 1, "like": 1}},
            },
        },
    )
    monkeypatch.setattr(manager, "collect_results", lambda *args, **kwargs: {})
    monkeypatch.setattr(manager, "dispatch_task", lambda request: {})
    monkeypatch.setattr(
        manager,
        "list_results",
        lambda: {
            "current_results": [
                {
                    "task_id": "uncertain",
                    "machine_id": "PC001",
                    "account_slot": "alpha",
                    "operation_task_id": task["operation_task_id"],
                    "capability": "home-engagement",
                    "item_results_status": "observed",
                    "item_results": [
                        {
                            "feed_id": "n1",
                            "browse": {"status": "success"},
                            "like": {"status": "unknown"},
                        }
                    ],
                }
            ]
        },
    )
    result = DailyOperationsRunner(ManagerAgentService(manager)).tick(
        task, datetime.fromisoformat("2026-09-09T10:00:00+08:00")
    )
    assert result["reserved_unknown"] == {"browse": 0, "like": 1}
    assert result["remaining"]["browse"] == 1 and result["excluded_feed_ids"] == ["n1"]


def test_daily_plan_runs_through_manager_envelopes_and_collected_receipts(tmp_path: Path) -> None:
    from tests.test_manager_agent_service import build_manager

    manager, transport = build_manager(tmp_path)
    manager.store.put(
        "accounts",
        "PC057:tesssst",
        {
            "machine_id": "PC057",
            "account_slot": "tesssst",
            "health_state": "READY",
            "current_uid": "uid-1",
            "platform": "xiaohongshu",
            "operations_config": {"daily_policy": {"action_spacing_seconds": 0}},
        },
    )
    invoke = transport.invoke

    def collect(machine: dict, action: str, payload: dict, **kwargs: object) -> dict:
        result = invoke(machine, action, payload, **kwargs)
        if action != "collect-results":
            return result
        for row in result["results"]:
            dispatched = next(r for r in transport.accepted if r["task_id"] == row["task_id"])
            row["completed_at"] = "2026-09-09T09:00:00+08:00"
            if dispatched["capability"] == "home-engagement":
                row["item_results"] = [
                    {
                        "feed_id": row["task_id"],
                        "browse": {"status": "success"},
                        "like": {
                            "status": "success"
                            if dispatched["parameters"]["like_count"]
                            else "skipped"
                        },
                    }
                ]
                row.pop("actual_count", None)
        return result

    transport.invoke = collect
    data = request()
    data["accounts"][0].update(machine_id="PC057", account_slot="tesssst")
    manager.save_daily_plan(data)
    outcome = manager.submit_daily_plan({"plan_date": data["plan_date"], "version": 1})
    identifier = outcome["submissions"][0]["operation_task_id"]
    runner = DailyOperationsRunner(ManagerAgentService(manager))
    for _ in range(8):
        task = manager.get_operation_task(identifier)["operation_task"]
        outcome = runner.tick(task, datetime.fromisoformat("2026-09-09T10:00:00+08:00"))
        if outcome["status"] == "COMPLETED":
            break
    assert outcome["status"] == "COMPLETED"
    assert len(transport.accepted) == 5
    assert all(r["parameters"]["daily_plan"]["plan_version"] == 1 for r in transport.accepted)
    assert outcome["actual"]["browse"] == 2 and outcome["actual"]["like"] == 1
    assert outcome["actual"]["comment"] == 1
    assert manager.get_goal(identifier)["goal"]["progress"]["actual_total"] == 4


def test_daily_runner_records_failed_dispatch_and_resumes_saved_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    task = submitted(manager)
    now = datetime.fromisoformat("2026-09-09T10:00:00+08:00")
    requests = []

    def dispatch(payload: dict) -> dict:
        requests.append(copy.deepcopy(payload))
        pending = len(requests) == 1
        execution = manager.store.put("tasks", payload["task_id"], {
            "task_id": payload["task_id"], "request": payload,
            "dispatch_state": "PENDING_DISPATCH" if pending else "ACCEPTED",
            "created_at": now.isoformat(), "message": "员工机离线" if pending else "",
        })
        return {"success": not pending, "status": execution["dispatch_state"], "task": execution}

    monkeypatch.setattr(manager, "dispatch_task", dispatch)
    monkeypatch.setattr(manager, "collect_results", lambda *args, **kwargs: {})
    first = DailyOperationsRunner(ManagerAgentService(manager)).tick(task, now)
    assert not first["success"]
    current = manager.get_operation_task(task["operation_task_id"])["operation_task"]
    assert current["daily_session"]["status"] == "PENDING_DISPATCH"
    assert current["daily_session"]["message"] == "员工机离线"
    second = DailyOperationsRunner(ManagerAgentService(manager)).tick(current, now)
    assert second["success"] and second["status"] == "DISPATCHED"
    assert requests[0] == requests[1]
    assert len(manager.list_tasks()["tasks"]) == 1
