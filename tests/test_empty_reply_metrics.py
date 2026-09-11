from __future__ import annotations

import pytest

from employee_operations import EmployeeOperations


@pytest.mark.parametrize("pending,expected", [(0, "无"), (1, "等待回执")])
def test_completed_empty_inspection(pending: int, expected: str) -> None:
    result = {
        "machine_id": "PC057", "account_slot": "tesssst", "capability": "reply-inbox",
        "state": "SUCCESS", "recorded_at": "2026-09-10T08:00:00Z",
        "business_result": {"collection_success": True, "frozen_event_count": pending,
                            "pending_count": pending},
    }
    metrics = EmployeeOperations({"daily_plans": {}, "incremental_records": {}},
                                 [result], lambda r: {}).day_metrics(
        "PC057", "tesssst", "2026-09-10"
    )
    for kind in ("reply", "private_reply"):
        assert metrics[kind]["status"] == expected
        assert metrics[kind].get("actual_display") == ("无" if not pending else None)


def test_uncollected_is_not_empty() -> None:
    metrics = EmployeeOperations({"daily_plans": {}, "incremental_records": {}},
                                 [], lambda r: {}).day_metrics(
        "PC057", "tesssst", "2026-09-10"
    )
    assert metrics["reply"]["actual"] is None
    assert metrics["reply"]["status"] == "等待回执"


def test_private_pending_does_not_hide_empty_comment_channel() -> None:
    result = {
        "machine_id": "PC057", "account_slot": "tesssst", "capability": "reply-inbox",
        "state": "SUCCESS", "recorded_at": "2026-09-10T08:00:00Z",
        "business_result": {"collection_success": True, "frozen_event_count": 1,
                            "pending_count": 1},
        "item_results": [{"action": "private_reply", "status": "PENDING"}],
    }
    metrics = EmployeeOperations({"daily_plans": {}, "incremental_records": {}},
                                 [result], lambda r: {}).day_metrics(
        "PC057", "tesssst", "2026-09-10"
    )
    assert metrics["reply"]["actual_display"] == "无"
    assert metrics["private_reply"].get("actual_display") is None
