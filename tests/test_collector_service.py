from __future__ import annotations

from scripts.collector_service import CollectorService
from scripts.operations_db import OperationsDatabase


def _service(tmp_path) -> CollectorService:
    return CollectorService(OperationsDatabase(path=tmp_path / "operations.db"))


def test_cursor_success_is_persisted_and_failure_keeps_progress(tmp_path) -> None:
    service = _service(tmp_path)
    success = service.record_success(
        account_slot="alpha",
        collector_type="note_comments",
        cursor_value="comment-20",
        last_seen_time="2026-08-19T11:00:00+00:00",
    )
    failed = service.record_failure(
        account_slot="alpha",
        collector_type="note_comments",
        error="页面暂时不可用",
    )

    assert success["last_success_time"]
    assert failed["cursor_value"] == "comment-20"
    assert failed["last_seen_time"] == "2026-08-19T11:00:00+00:00"
    assert failed["last_error"] == "页面暂时不可用"


def test_collectors_are_isolated_by_account_and_type(tmp_path) -> None:
    service = _service(tmp_path)
    service.record_success(
        account_slot="alpha",
        collector_type="note_comments",
        cursor_value="a-1",
    )
    service.record_success(
        account_slot="beta",
        collector_type="account_metrics",
        cursor_value="b-1",
    )

    assert service.get("alpha", "note_comments")["cursor_value"] == "a-1"
    assert service.get("beta", "account_metrics")["cursor_value"] == "b-1"
    assert len(service.list()) == 2
