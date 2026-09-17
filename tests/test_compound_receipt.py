from __future__ import annotations

import json

from compound_receipt import confirmed_action_counts


def test_readonly_reconciliation_does_not_count_as_new_browse() -> None:
    from manager_service import ManagerService
    from operations_acceptance import confirmed_counts

    request = {"capability": "get-feed-detail"}
    result = {
        "state": "SUCCESS",
        "task_request": request,
        "business_result_status": "observed",
        "business_result": {
            "original_task_id": "original",
            "reconciliation": "CONFIRMED_SUCCESS",
            "new_business_actions": 0,
        },
    }
    assert confirmed_action_counts(result) == {}
    assert confirmed_counts(result, request) == {}
    assert ManagerService._result_metric_counts(result) == {}


from employee_operations import EmployeeOperations
from manager_database import ManagerDatabase
from manager_service import ManagerService
from operations_acceptance import confirmed_counts


def compound_result() -> dict:
    items = [
        {"action": action, "status": "SUCCESS", "success": True, "platform_readback": True}
        for action in [*["browse"] * 12, "like", "like", "comment", "comment", "friend_request"]
    ]
    items.extend(
        [
            {"action": "friend_request", "status": "RESULT_UNKNOWN", "success": False},
            {"action": "reply", "status": "SKIPPED", "success": False},
            {"action": "private_reply", "status": "SKIPPED", "success": False},
            {"action": "collect-operations-metrics", "status": "SUCCESS", "success": True},
        ]
    )
    return {
        "machine_id": "PC057",
        "account_slot": "ceshi3",
        "task_id": "compound",
        "remote_execution_id": "compound",
        "state": "RESULT_UNKNOWN",
        "recorded_at": "2026-09-11T15:17:30+08:00",
        "capability": "home-engagement",
        "item_results_status": "observed",
        "item_results": items,
        "business_result": {"metrics": {"browse": 999, "friend_request": 2}},
    }


def test_visual_follow_receipt_counts_platform_confirmed_actions() -> None:
    result = {
        "capability": "browser-action",
        "state": "SUCCESS",
        "item_results": [
            {"action": "follow", "status": "SUCCESS", "platform_readback": True,
             "changed": True},
            {"action": "follow", "status": "SUCCESS", "platform_readback": True,
             "changed": True},
            {"action": "follow", "status": "ALREADY_FOLLOWED", "changed": False,
             "platform_readback": True},
        ],
    }
    assert confirmed_action_counts(result) == {"friend_request": 2}
    assert ManagerService._result_metric_counts(result) == {"friend_request": 2}
    assert confirmed_counts(result, {"capability": "browser-action"}) == {"friend_request": 2}


def test_completed_browse_counts_without_platform_state_change() -> None:
    result = {
        "capability": "home-engagement",
        "item_results": [
            {"action": "browse", "status": "SUCCESS", "changed": False},
            {"action": "like", "status": "SUCCESS", "changed": False,
             "platform_readback": True},
        ],
    }
    assert confirmed_action_counts(result) == {"browse": 1, "like": 0}
    assert ManagerService._result_metric_counts(result) == {"browse": 1, "like": 0}


def test_compound_counts_and_unknown_status_reach_daily_database(tmp_path):
    result = compound_result()
    expected = {
        "browse": 12,
        "like": 2,
        "comment": 2,
        "friend_request": 1,
        "reply": 0,
        "private_reply": 0,
    }
    assert ManagerService._result_metric_counts(result) == expected
    assert confirmed_counts(result, {}) == expected
    state = {
        "results": {"compound": result},
        "daily_plans": {},
        "incremental_records": {},
        "personas": {},
        "accounts": {"PC057:ceshi3": {"machine_id": "PC057", "account_slot": "ceshi3"}},
    }
    operations = EmployeeOperations(state, [result], ManagerService._result_metric_counts)
    metrics = operations.day_metrics("PC057", "ceshi3", "2026-09-11")
    assert {key: metrics[key]["actual"] for key in expected} == expected
    assert metrics["friend_request"]["status"] == "执行结果待核对"
    assert metrics["comment"]["status"] == "已回传"
    assert operations.day_metrics("PC057", "other", "2026-09-11")["browse"]["actual"] is None
    database = ManagerDatabase(tmp_path)
    database.sync(state)
    database.sync(state)
    import sqlite3

    with sqlite3.connect(database.path) as db:
        rows = db.execute(
            "SELECT metric, actual FROM daily_metric_facts WHERE business_date = ?",
            ("2026-09-11",),
        ).fetchall()
    assert {key: value for key, value in rows if key in expected} == expected
    assert json.loads(json.dumps(result))["state"] == "RESULT_UNKNOWN"


def test_compound_requires_item_confirmation_and_keeps_legacy_parser():
    result = compound_result()
    result["item_results"] = [
        {"action": "comment", "status": "SUCCESS"},
        {
            "action": "friend_request",
            "status": "RESULT_UNKNOWN",
            "success": True,
            "platform_readback": True,
        },
        {"action": "like", "status": "SUCCESS", "platform_readback": True, "changed": False},
    ]
    assert confirmed_action_counts(result) == {"like": 0}
    result["item_results"] = [{"browse": {"status": "success"}, "like": {"success": True}}]
    assert confirmed_action_counts(result) is None
    assert ManagerService._result_metric_counts(result) == {"browse": 1, "like": 1}


def test_employee_action_names_and_read_only_browse():
    result = compound_result()
    result.update(
        state="PARTIAL_SUCCESS",
        item_results=[
            *[{"action": "browse", "status": "SUCCESS", "read_seconds": 21.9}] * 12,
            {"action": "browse", "status": "SKIPPED"},
            *[{"action": "follow", "status": "SUCCESS", "platform_readback": True}] * 2,
            {"action": "inbound-comment-reply", "status": "BLOCKED"},
            {"action": "private-message-reply", "status": "SKIPPED"},
            {"action": "follow", "status": "RESULT_UNKNOWN", "platform_readback": True},
        ],
    )
    assert confirmed_action_counts(result) == {
        "browse": 12,
        "friend_request": 2,
        "reply": 0,
        "private_reply": 0,
    }
    state = {"results": {"compound": result}, "daily_plans": {}, "incremental_records": {}}
    metrics = EmployeeOperations(state, [result], ManagerService._result_metric_counts).day_metrics(
        "PC057", "ceshi3", "2026-09-11"
    )
    assert metrics["reply"]["status"] == "已阻塞"
    assert metrics["private_reply"]["status"] == "已跳过"
    assert metrics["friend_request"]["status"] == "执行结果待核对"


def test_interrupted_home_session_is_unresolved():
    result = compound_result()
    result["item_results"] = [
        {"operation": "home-engagement", "status": "RESULT_UNKNOWN", "success": False},
    ]
    assert confirmed_action_counts(result) == {}
    state = {"results": {"compound": result}, "daily_plans": {}, "incremental_records": {}}
    metrics = EmployeeOperations(state, [result], ManagerService._result_metric_counts).day_metrics(
        "PC057", "ceshi3", "2026-09-11"
    )
    assert metrics["browse"]["actual"] is None
    assert metrics["browse"]["status"] == "执行结果待核对"
    assert metrics["like"]["status"] == "执行结果待核对"
    assert metrics["comment"]["status"] == "任务已结束 · 未提供逐项结果"
