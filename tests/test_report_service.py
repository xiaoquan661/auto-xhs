from __future__ import annotations

from scripts.metric_service import METRIC_FIELDS, MetricService
from scripts.operations_db import OperationsDatabase
from scripts.report_service import ReportService


def _services(tmp_path) -> tuple[MetricService, ReportService]:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    return MetricService(database), ReportService(database)


def _record_note(
    metrics: MetricService,
    *,
    note_id: str,
    captured_at: str,
    likes: int,
    comments: int | None,
) -> None:
    metrics.record_snapshot(
        account_slot="alpha",
        entity_type="note",
        entity_id=note_id,
        source="note_detail",
        captured_at=captured_at,
        metrics={"likes": likes, "comments": comments},
    )


def test_report_uses_adjacent_valid_snapshots_and_preserves_missing_metrics(
    tmp_path,
) -> None:
    metrics, reports = _services(tmp_path)
    _record_note(
        metrics,
        note_id="note-1",
        captured_at="2026-08-28T08:00:00+00:00",
        likes=10,
        comments=None,
    )
    _record_note(
        metrics,
        note_id="note-1",
        captured_at="2026-08-28T10:00:00+00:00",
        likes=14,
        comments=2,
    )
    _record_note(
        metrics,
        note_id="note-1",
        captured_at="2026-08-28T12:00:00+00:00",
        likes=18,
        comments=5,
    )

    report = reports.generate(
        report_type="daily",
        account_slot="alpha",
        period_start="2026-08-28T00:00:00+00:00",
        period_end="2026-08-28T23:59:59+00:00",
    )

    detail = report["details"][0]
    assert detail["increments"]["likes"] == 8
    assert detail["increments"]["comments"] == 3
    assert detail["increments"]["favorites"] is None
    assert report["summary"]["increments"]["favorites"] is None
    assert report["summary"]["increments"] == {
        metric: sum(
            item["increments"][metric]
            for item in report["details"]
            if item["increments"][metric] is not None
        )
        if any(item["increments"][metric] is not None for item in report["details"])
        else None
        for metric in METRIC_FIELDS
    }
    missing_comment = next(
        item
        for item in report["anomalies"]
        if item["type"] == "missing_metric" and item["metric"] == "comments"
    )
    assert missing_comment["entity_id"] == "note-1"
    assert missing_comment["snapshot_id"] == detail["snapshot_ids"][0]


def test_note_review_only_uses_requested_note(tmp_path) -> None:
    metrics, reports = _services(tmp_path)
    for note_id in ("note-1", "note-2"):
        _record_note(
            metrics,
            note_id=note_id,
            captured_at="2026-08-28T08:00:00+00:00",
            likes=10,
            comments=1,
        )
        _record_note(
            metrics,
            note_id=note_id,
            captured_at="2026-08-28T10:00:00+00:00",
            likes=12,
            comments=2,
        )

    report = reports.generate(
        report_type="note_review",
        account_slot="alpha",
        entity_id="note-2",
        period_start="2026-08-28T00:00:00+00:00",
        period_end="2026-08-28T23:59:59+00:00",
    )

    assert report["summary"]["entity_count"] == 1
    assert report["details"][0]["entity_id"] == "note-2"


def test_report_revision_is_insert_only_and_keeps_original_content(tmp_path) -> None:
    metrics, reports = _services(tmp_path)
    _record_note(
        metrics,
        note_id="note-1",
        captured_at="2026-08-28T08:00:00+00:00",
        likes=10,
        comments=1,
    )
    _record_note(
        metrics,
        note_id="note-1",
        captured_at="2026-08-28T10:00:00+00:00",
        likes=14,
        comments=2,
    )
    original = reports.generate(
        report_type="weekly",
        account_slot="alpha",
        period_start="2026-08-25T00:00:00+00:00",
        period_end="2026-08-31T23:59:59+00:00",
    )
    original_summary = dict(original["summary"])
    _record_note(
        metrics,
        note_id="note-1",
        captured_at="2026-08-28T12:00:00+00:00",
        likes=20,
        comments=4,
    )

    revision = reports.revise(original["report_id"], revision_reason="补录中午快照")

    assert reports.get(original["report_id"])["summary"] == original_summary
    assert revision["report_id"] != original["report_id"]
    assert revision["root_report_id"] == original["report_id"]
    assert revision["previous_report_id"] == original["report_id"]
    assert revision["revision_number"] == 2
    assert revision["summary"]["increments"]["likes"] == 10
    assert len(reports.list(account_slot="alpha")) == 2
