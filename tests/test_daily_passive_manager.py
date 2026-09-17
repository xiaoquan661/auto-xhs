from __future__ import annotations

from types import SimpleNamespace

from daily_passive_collection import DailyCollector
from manager_service import ManagerService
from manager_store import ManagerStore


def test_daily_receipt_imports_without_manager_dispatch_and_deduplicates(tmp_path):
    collector = DailyCollector.__new__(DailyCollector)
    emitted = []
    collector.worker = SimpleNamespace(
        _publish_outbox_record=lambda *args, **kwargs: emitted.append(args[3]["result"])
    )
    event = {
        "event_type": "received_like",
        "platform_event_id": "test-like-1",
        "occurred_at": "2026-09-15T12:00:00+08:00",
        "payload": {},
    }
    record = {
        "task_id": "pc001-a-daily-passive-2026-09-15",
        "machine_id": "PC001",
        "account_slot": "a",
        "platform": "rednote",
        "day": "2026-09-15",
        "started_at": "2026-09-16T00:10:00+08:00",
        "channels": [],
        "collections": [{"result": {"snapshots": {"events": [event]}}}],
    }
    collector.finish(record, tmp_path / "receipt.json", "SUCCESS", "采集完成")
    manager = ManagerService(store=ManagerStore(tmp_path / "manager"))
    first = manager._ingest_results({"machine_id": "PC001"}, emitted, download_evidence=False)
    second = manager._ingest_results({"machine_id": "PC001"}, emitted, download_evidence=False)
    assert first["collected"] == first["incremental_collected"] == 1
    assert second["collected"] == second["incremental_collected"] == 0
    assert second["duplicates"] == second["incremental_duplicates"] == 1
