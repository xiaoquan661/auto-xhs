from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import manager_dashboard_reader
import pytest
from manager_dashboard_reader import ManagerDashboardReader
from manager_store import ManagerStore


def test_overlapping_requests_share_one_dashboard_calculation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callers = Barrier(2)

    class Pending(Future[dict]):
        def result(self, timeout: float | None = None) -> dict:
            callers.wait(timeout=5)
            return {"success": True}

    class Pool:
        def __init__(self, **kwargs: object) -> None:
            self.submissions = 0

        def submit(self, *args: object) -> Pending:
            self.submissions += 1
            return Pending()

        def shutdown(self, **kwargs: object) -> None:
            pass

    monkeypatch.setattr(manager_dashboard_reader, "ProcessPoolExecutor", Pool)
    reader = ManagerDashboardReader(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as threads:
        requests = [threads.submit(reader.snapshot) for _ in range(2)]
        assert all(request.result(timeout=10)["success"] for request in requests)
    assert reader.pool.submissions == 1
    reader.close()


def test_reader_loads_new_committed_metrics_on_each_request(tmp_path):
    store = ManagerStore(root=tmp_path)
    reader = ManagerDashboardReader(tmp_path)
    try:
        assert reader.snapshot()["employee_summary"]["collection_records"] == []
        store.put(
            "results",
            "result1",
            {
                "record_id": "result1",
                "task_id": "t1",
                "machine_id": "PC071",
                "account_slot": "3-3",
                "state": "SUCCESS",
                "business_result": {
                    "source": "creator_manager",
                    "captured_at": "2026-10-08T05:11:00+00:00",
                    "notes": [{"entity_id": "note1", "metrics": {"views": 42}}],
                },
            },
        )
        rows = reader.snapshot()["employee_summary"]["collection_records"]
        assert len(rows) == 1
        assert rows[0]["metrics"]["views"] == 42
    finally:
        reader.close()
