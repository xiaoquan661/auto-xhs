from __future__ import annotations

from pathlib import Path

import pytest
from manager_store import ManagerStore


def test_nested_view_preserves_live_snapshot_and_projected_fields(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    store.put("tasks", "live", {
        "task_id": "live", "state": "ACCEPTED", "request": {"memory_context": "large-context"},
    })
    store.record_result("receipt", {
        "task_id": "live", "state": "SUCCESS", "task_request": {"memory_context": "large-context"},
    })
    view = store.read_view(excluded_fields=frozenset({"memory_context"}), copy_on_read=False)
    store.update("tasks", "live", lambda row: {**row, "state": "RUNNING"})
    nested = view.read_view(copy_on_read=False)
    assert nested.get("tasks", "live")["state"] == "ACCEPTED"
    assert nested.get("tasks", "live")["request"] == {}
    assert nested.get("results", "receipt")["task_request"] == {}
    assert store.read_view().get("tasks", "live")["state"] == "RUNNING"


def test_dashboard_view_reads_published_file_without_transaction_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ManagerStore(tmp_path)
    store.put("accounts", "a", {"name": "before"})
    monkeypatch.setattr(store, "_transaction", lambda *a, **k: pytest.fail("读取等待了写锁"))
    view = store.read_view()
    ManagerStore(tmp_path).put("accounts", "a", {"name": "after"})
    assert view.get("accounts", "a")["name"] == "before"
    assert view.read_view().get("accounts", "a")["name"] == "before"
    assert store.read_view().get("accounts", "a")["name"] == "after"


@pytest.mark.parametrize("identifier", ["DAILY-20260922-PC057-ceshi3", "OP-UPPER"])
def test_operation_task_preserves_stored_identifier(tmp_path: Path, identifier: str) -> None:
    from manager_service import ManagerService

    store = ManagerStore(tmp_path)
    store.put("operation_tasks", identifier, {"operation_task_id": identifier, "state": "RUNNING"})
    service = ManagerService(store=store)
    requested = identifier.lower() if identifier == "OP-UPPER" else identifier
    assert service._get_operation_task(requested)["operation_task_id"] == identifier
    service._update_operation_task(requested, state="COMPLETED")
    assert store.get("operation_tasks", identifier)["state"] == "COMPLETED"
    assert len(store.list("operation_tasks")) == 1
