from __future__ import annotations

from pathlib import Path

import pytest
from manager_store import ManagerStore


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
