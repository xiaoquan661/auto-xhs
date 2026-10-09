from __future__ import annotations

import json

from manager_database import ManagerDatabase
from manager_store import ManagerStore


def test_new_task_and_receipt_share_database_and_dashboard_source(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    old_task = {"task_id": "existing", "machine_id": "PC001", "state": "RUNNING"}
    store.mutate(lambda state: state["tasks"].update(existing=old_task))

    task = {
        "task_id": "new",
        "machine_id": "PC001",
        "request": {"account_slot": "a"},
        "state": "PENDING_DISPATCH",
    }
    store.put("tasks", "new", task)
    with store.batch():
        store.update("tasks", "new", lambda row: {**row, "state": "SUCCESS"})
        receipt = {"task_id": "new", "machine_id": "PC001", "account_slot": "a", "state": "SUCCESS"}
        stored, created = store.record_result("receipt-1", receipt)
        assert created and stored == receipt

    state_file = json.loads((tmp_path / "manager-state.json").read_text(encoding="utf-8"))
    assert set(state_file["tasks"]) == {"existing"}
    assert state_file["results"] == {}
    assert store.read_view().get("tasks", "new")["state"] == "SUCCESS"
    assert store.read_view().get("results", "receipt-1") == receipt

    database = ManagerDatabase(tmp_path)
    task_rows = database.query(
        "SELECT json_extract(payload,'$.state') AS state FROM records "
        "WHERE collection='tasks' AND record_key='new'"
    )["rows"]
    assert task_rows == [{"state": "SUCCESS"}]
    assert database.query("SELECT record_key FROM records WHERE collection='results'")["rows"] == [
        {"record_key": "receipt-1"}
    ]

    database.sync(store.snapshot())
    assert database.query(
        "SELECT record_key FROM records WHERE collection='tasks' ORDER BY record_key"
    )["rows"] == [{"record_key": "existing"}, {"record_key": "new"}]
    assert store.get("tasks", "existing") == old_task


def test_live_task_update_is_visible_after_store_reopen(tmp_path) -> None:
    first = ManagerStore(tmp_path)
    first.put("tasks", "new", {"task_id": "new", "machine_id": "PC001", "state": "ACCEPTED"})

    second = ManagerStore(tmp_path)
    second.update("tasks", "new", lambda row: {**row, "state": "RUNNING"})

    assert first.get("tasks", "new")["state"] == "RUNNING"
    assert first.snapshot()["tasks"]["new"]["state"] == "RUNNING"


def test_receipt_can_match_new_task_by_remote_execution(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    store.put(
        "tasks",
        "new",
        {"task_id": "new", "machine_id": "PC001", "remote_execution_id": "remote-1"},
    )
    receipt = {"remote_execution_id": "remote-1", "machine_id": "PC001"}
    store.record_result("receipt-1", receipt)

    assert store.get("results", "receipt-1") == receipt
    assert not (tmp_path / "manager-state.json").exists()
