from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from comment_receipt_review import pending_comment_attribution, review_comments
from daily_operations_runner import DailyOperationsRunner
from employee_operations import EmployeeOperations
from manager_agent_service import ManagerAgentService
from manager_service import ManagerService
from manager_store import ManagerStore
from service_errors import ServiceError

from tests.test_daily_operations_plan import manager_at, submitted


def receipt(number: int = 1) -> dict:
    return {
        "record_id": f"original-{number}", "machine_id": "PC001", "account_slot": "alpha",
        "task_id": f"task-{number}", "remote_execution_id": f"execution-{number}",
        "result_revision": 1, "sequence": number, "state": "BLOCKED",
        "capability": "post-comment", "recorded_at": "2026-09-09T13:49:00+08:00",
        "task_request": {"capability": "post-comment", "parameters": {"count": 2}},
        "item_results": [{
            "status": "BLOCKED", "success": False, "comment_id": f"comment-{number}",
            "platform_readback": True, "submitted": True, "identity_match": False,
            "expected_uid": "old-uid", "actual_uid": "observed-uid",
        }, {"status": "BLOCKED", "success": False, "reason": "第二条未执行"}],
    }


def test_review_three_submitted_comments_preserves_history_and_business_date(
    tmp_path: Path,
) -> None:
    manager = ManagerService(store=ManagerStore(tmp_path))
    for number in range(1, 4):
        manager.store.put("results", f"original-{number}", receipt(number))
        manager.store.put("tasks", f"task-{number}", {
            "task_id": f"task-{number}", "business_state": "BLOCKED",
        })
    request = {
        "machine_id": "PC001", "account_slot": "alpha",
        "record_ids": [f"original-{n}" for n in range(1, 4)], "reason": "用户核对三条评论归属",
    }
    reviewed = review_comments(manager.store, request)["results"]
    assert len(manager.list_results()["result_history"]) == 6
    assert len(manager.list_results()["current_results"]) == 3
    assert manager.store.get("results", "original-1") == receipt()
    view = EmployeeOperations(
        manager.store.snapshot(), manager.list_results()["current_results"],
        manager._result_metric_counts,
    )
    assert sum(view.action_counts(r)["comment"] for r in reviewed) == 3
    assert all(view.result_date(r) == "2026-09-09" for r in reviewed)
    assert all(r["state"] == "PARTIAL_SUCCESS" for r in reviewed)
    assert not any(pending_comment_attribution(r) for r in reviewed)
    assert reviewed[0]["item_results"][0]["actual_uid"] == "observed-uid"
    assert review_comments(manager.store, request)["results"] == reviewed
    assert len(manager.store.list("results")) == 6
    manager._apply_collected_result(receipt())
    assert manager.store.get("tasks", "task-1")["business_state"] == "PARTIAL_SUCCESS"


def test_unknown_submission_cannot_be_credited_by_attribution_review(tmp_path: Path) -> None:
    store = ManagerStore(tmp_path)
    row = receipt()
    row["item_results"][0]["platform_readback"] = False
    store.put("results", "original-1", row)
    with pytest.raises(ServiceError, match="证据"):
        review_comments(store, {
            "machine_id": "PC001", "account_slot": "alpha", "record_ids": ["original-1"],
            "reason": "核对归属",
        })
    assert len(store.list("results")) == 1


def test_daily_runner_waits_for_submitted_comment_attribution(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    task = submitted(manager)
    # An unresolved submission also blocks a new daily plan for the same slot.
    manager.store.put("results", "original-1", receipt())
    calls = []
    monkeypatch.setattr(manager, "dispatch_task", lambda request: calls.append(request))
    runner = DailyOperationsRunner(ManagerAgentService(manager, agent_id="agent"))
    result = runner.tick(task, datetime.fromisoformat("2026-09-09T14:00:00+08:00"))
    assert result["status"] == "WAITING_RECONCILIATION"
    assert result["pending_attribution"] == ["original-1"]
    assert calls == []


def test_other_slot_attribution_does_not_block_daily_dispatch(tmp_path: Path, monkeypatch) -> None:
    manager = manager_at(tmp_path, monkeypatch)
    task = submitted(manager)
    row = receipt()
    row["account_slot"] = "beta"
    manager.store.put("results", "original-1", row)
    calls = []

    def dispatch(request: dict) -> dict:
        calls.append(request)
        return {"success": True}

    monkeypatch.setattr(manager, "dispatch_task", dispatch)
    runner = DailyOperationsRunner(ManagerAgentService(manager, agent_id="agent"))
    result = runner.tick(task, datetime.fromisoformat("2026-09-09T14:00:00+08:00"))
    assert result["status"] == "DISPATCHED"
    assert len(calls) == 1
