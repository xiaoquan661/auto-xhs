from __future__ import annotations

from scripts.operation_event_service import OperationEventService
from scripts.operations_db import OperationsDatabase


def test_operation_result_is_structured_and_queryable(tmp_path) -> None:
    service = OperationEventService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )
    task = {
        "task_id": "task-1",
        "account_slot": "alpha",
        "capability": "reply-comment",
        "target_type": "comment",
        "target_id": "comment-1",
        "started_at": "2026-08-19T10:00:00+00:00",
        "finished_at": "2026-08-19T10:00:02+00:00",
        "state": "SUCCESS",
    }

    event = service.record(
        task,
        result={"message": "回复发送成功"},
        readback={"reply_found": True},
    )

    assert event["platform_result"]["message"] == "回复发送成功"
    assert event["readback"]["reply_found"] is True
    assert service.list(account_slot="alpha")[0]["target_id"] == "comment-1"
