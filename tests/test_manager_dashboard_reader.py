from __future__ import annotations

from manager_dashboard_reader import ManagerDashboardReader
from manager_store import ManagerStore


def test_reader_loads_new_committed_metrics_on_each_request(tmp_path):
    store = ManagerStore(root=tmp_path)
    reader = ManagerDashboardReader(tmp_path)
    try:
        assert reader.snapshot()["employee_summary"]["collection_records"] == []
        store.put("results", "result1", {
            "record_id": "result1", "task_id": "t1", "machine_id": "PC071",
            "account_slot": "3-3", "state": "SUCCESS",
            "business_result": {
                "source": "creator_manager", "captured_at": "2026-10-08T05:11:00+00:00",
                "notes": [{"entity_id": "note1", "metrics": {"views": 42}}],
            },
        })
        rows = reader.snapshot()["employee_summary"]["collection_records"]
        assert len(rows) == 1
        assert rows[0]["metrics"]["views"] == 42
    finally:
        reader.close()
