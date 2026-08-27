from __future__ import annotations

from scripts.data_recovery_service import DataRecoveryService
from scripts.operations_db import OperationsDatabase


def test_summary_reports_three_business_ledgers(tmp_path) -> None:
    service = DataRecoveryService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )
    service.published_posts.record(
        {
            "task_id": "publish-1",
            "account_slot": "alpha",
            "capability": "fill-publish",
            "state": "SUCCESS",
            "finished_at": "2026-08-26T10:00:00+00:00",
            "parameters": {"preview": {"title": "标题"}},
        },
        result={"verified": True, "note_id": "note-1"},
    )
    service.operation_events.record(
        {
            "task_id": "like-1",
            "account_slot": "alpha",
            "capability": "like-feed",
            "target_id": "note-1",
            "state": "SUCCESS",
            "finished_at": "2026-08-26T10:01:00+00:00",
        }
    )

    summary = service.summary()

    assert summary["published_posts"]["total"] == 1
    assert summary["operation_records"]["states"]["SUCCESS"] == 1
    assert summary["operation_actions"]["like-feed"] == 1
    assert summary["reply_records"]["total"] == 0
