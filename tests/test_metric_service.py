from __future__ import annotations

from scripts.metric_service import MetricService
from scripts.operations_db import OperationsDatabase


def _service(tmp_path) -> MetricService:
    return MetricService(OperationsDatabase(path=tmp_path / "operations.db"))


def test_snapshot_is_idempotent_for_same_entity_time_and_source(tmp_path) -> None:
    service = _service(tmp_path)
    values = {
        "account_slot": "alpha",
        "entity_type": "note",
        "entity_id": "note-1",
        "source": "note_detail",
        "captured_at": "2026-08-19T12:00:00+00:00",
        "metrics": {"likes": 10, "favorites": 3, "comments": 2},
    }

    first = service.record_snapshot(**values)
    duplicate = service.record_snapshot(**values)

    assert first["created"] is True
    assert duplicate["created"] is False
    assert duplicate["snapshot"]["snapshot_id"] == first["snapshot"]["snapshot_id"]


def test_latest_delta_preserves_missing_values(tmp_path) -> None:
    service = _service(tmp_path)
    service.record_snapshot(
        account_slot="alpha",
        entity_type="note",
        entity_id="note-1",
        source="note_detail",
        captured_at="2026-08-19T12:00:00+00:00",
        metrics={"likes": 10, "favorites": 3},
    )
    service.record_snapshot(
        account_slot="alpha",
        entity_type="note",
        entity_id="note-1",
        source="note_detail",
        captured_at="2026-08-19T13:00:00+00:00",
        metrics={"likes": 14, "favorites": 5, "comments": 2},
    )

    delta = service.latest_delta(
        account_slot="alpha",
        entity_type="note",
        entity_id="note-1",
    )

    assert delta["delta"]["likes"] == 4
    assert delta["delta"]["favorites"] == 2
    assert delta["delta"]["comments"] is None
