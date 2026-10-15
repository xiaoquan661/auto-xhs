from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Thread

import pytest
from manager_database import ManagerDatabase
from manager_store import ManagerStore
from run_lock import RunLock


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

    store.delete("tasks", "existing")
    database.sync(store.snapshot())
    assert database.query("SELECT record_key FROM records WHERE collection='tasks'")["rows"] == [
        {"record_key": "new"}
    ]
    assert database.query("SELECT record_key FROM records WHERE collection='results'")["rows"] == [
        {"record_key": "receipt-1"}
    ]


def test_live_task_update_is_visible_after_store_reopen(tmp_path) -> None:
    first = ManagerStore(tmp_path)
    first.put("tasks", "new", {"task_id": "new", "machine_id": "PC001", "state": "ACCEPTED"})

    second = ManagerStore(tmp_path)
    second.update("tasks", "new", lambda row: {**row, "state": "RUNNING"})

    assert first.get("tasks", "new")["state"] == "RUNNING"
    assert first.snapshot()["tasks"]["new"]["state"] == "RUNNING"


def test_read_view_keeps_its_live_task_snapshot(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    store.put("tasks", "new", {"task_id": "new", "state": "ACCEPTED"})
    view = store.read_view()
    store.update("tasks", "new", lambda row: {**row, "state": "RUNNING"})
    assert view.get("tasks", "new")["state"] == "ACCEPTED"
    assert view.list("tasks")[0]["state"] == "ACCEPTED"


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


def test_live_task_and_receipt_commit_while_legacy_file_is_locked(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    store.put("tasks", "new", {"task_id": "new", "state": "ACCEPTED"})
    lock = RunLock(str(store.lock_path))
    assert lock.acquire(timeout=1)
    try:
        store.update("tasks", "new", lambda row: {**row, "state": "RUNNING"})
        store.put("tasks", "next", {"task_id": "next", "state": "ACCEPTED"})
        receipt = {"task_id": "new", "state": "SUCCESS"}
        assert store.record_result("receipt", receipt) == (receipt, True)
        assert store.get("tasks", "new")["state"] == "RUNNING"
    finally:
        lock.release()


def test_batch_and_concurrent_live_update_preserve_both_changes(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    store.put("tasks", "new", {"task_id": "new", "state": "ACCEPTED", "request": {"a": 1}})
    started = Event()
    release = Event()

    def batch_update() -> None:
        with store.batch():
            store.mutate(
                lambda state: state["tasks"]["new"].update(state="SUCCESS", request={"a": 2})
            )
            started.set()
            assert release.wait(3)

    thread = Thread(target=batch_update)
    thread.start()
    try:
        assert started.wait(3)
        other = ManagerStore(tmp_path)
        other.update(
            "tasks",
            "new",
            lambda row: {
                **row,
                "remote_execution_id": "remote",
                "request": {**row["request"], "b": 3},
            },
        )
    finally:
        release.set()
        thread.join(3)
    current = store.get("tasks", "new")
    assert current["state"] == "SUCCESS"
    assert current["remote_execution_id"] == "remote"
    assert current["request"] == {"a": 2, "b": 3}


def test_concurrent_receipt_replays_insert_once(tmp_path) -> None:
    ManagerStore(tmp_path).put("tasks", "new", {"task_id": "new"})
    receipt = {"task_id": "new", "state": "SUCCESS"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        writes = list(
            pool.map(
                lambda _: ManagerStore(tmp_path).record_result("receipt", receipt),
                range(2),
            )
        )
    assert sorted(created for _, created in writes) == [False, True]
    assert all(row == receipt for row, _ in writes)


def test_point_reads_do_not_load_unrelated_live_receipts(tmp_path, monkeypatch) -> None:
    store = ManagerStore(tmp_path)
    store.put("machines", "PC001", {"state": "READY"})
    store.put("tasks", "new", {"task_id": "new", "state": "ACCEPTED"})

    def fail_load() -> None:
        raise AssertionError("point read loaded all live records")

    monkeypatch.setattr(store.live, "load", fail_load)
    assert store.get("machines", "PC001")["state"] == "READY"
    assert store.get("tasks", "new")["state"] == "ACCEPTED"
    assert store.get_collection_cursor("PC001") == 0


def test_batch_releases_json_lock_before_sqlite_commit(tmp_path, monkeypatch) -> None:
    store = ManagerStore(tmp_path)
    original_commit = store.live.commit

    def assert_json_released(*args, **kwargs) -> None:
        lock = RunLock(str(store.lock_path))
        assert lock.acquire(timeout=0.2)
        lock.release()
        original_commit(*args, **kwargs)

    monkeypatch.setattr(store.live, "commit", assert_json_released)
    with store.batch():
        store.put("tasks", "new", {"task_id": "new", "state": "ACCEPTED"})
        store.put("machines", "PC001", {"state": "READY"})
    assert store.get("tasks", "new")["state"] == "ACCEPTED"


def test_batch_receipt_keeps_concurrent_first_receipt(tmp_path) -> None:
    store = ManagerStore(tmp_path)
    store.put("tasks", "new", {"task_id": "new"})
    first = {"task_id": "new", "state": "SUCCESS", "collected_at": "first"}
    replay = {**first, "collected_at": "replay"}
    with store.batch():
        store.record_result("receipt", replay)
        ManagerStore(tmp_path).record_result("receipt", first)
    assert store.get("results", "receipt") == first


def test_receipt_memory_import_runs_after_json_commit(tmp_path, monkeypatch) -> None:
    from manager_service import ManagerService

    store = ManagerStore(tmp_path)
    store.put("tasks", "new", {"task_id": "new", "request": {"account_slot": "a"}})
    service = ManagerService(store=store)
    imported = []

    def import_memory(result: dict) -> dict:
        lock = RunLock(str(store.lock_path))
        assert lock.acquire(timeout=0.2)
        lock.release()
        assert store.get("tasks", "new")["business_state"] == "SUCCESS"
        imported.append(result["task_id"])
        return {"success": True}

    monkeypatch.setattr(service, "ingest_memory", import_memory)
    result = service._ingest_results(
        {"machine_id": "PC001"},
        [{"task_id": "new", "remote_execution_id": "remote", "state": "SUCCESS"}],
        download_evidence=False,
    )
    assert result["collected"] == 1
    assert imported == ["new"]


@pytest.mark.parametrize("terminal_before_ack", [False, True])
def test_batch_ack_keeps_concurrent_terminal_task_state(tmp_path, terminal_before_ack) -> None:
    from manager_service import ManagerService

    store = ManagerStore(tmp_path)
    store.put(
        "tasks",
        "new",
        {
            "task_id": "new",
            "machine_id": "PC001",
            "state": "TRANSFERRED",
            "dispatch_state": "TRANSFERRED",
            "business_state": "",
        },
    )
    manager = ManagerService(store=store)
    other = ManagerService(store=ManagerStore(tmp_path))

    def finish_task() -> dict:
        return other._update_task(
            "new",
            dispatch_state="COMPLETED",
            business_state="SUCCESS",
            last_known_remote_state="SUCCESS",
            reconciliation_required=False,
        )

    with store.batch():
        if terminal_before_ack:
            terminal = finish_task()
        manager._update_task(
            "new",
            dispatch_state="ACKNOWLEDGED",
            last_known_remote_state="ACCEPTED",
        )
        if not terminal_before_ack:
            terminal = finish_task()
    current = store.get("tasks", "new")
    for key in (
        "state",
        "dispatch_state",
        "business_state",
        "last_known_remote_state",
        "reconciliation_required",
        "updated_at",
    ):
        assert current[key] == terminal[key]
    manager._update_task(
        "new", business_state="PARTIAL_SUCCESS", last_known_remote_state="PARTIAL_SUCCESS"
    )
    assert store.get("tasks", "new")["state"] == "PARTIAL_SUCCESS"


def test_agent_message_memory_import_runs_after_batch_commit(tmp_path, monkeypatch) -> None:
    from manager_service import ManagerService

    store = ManagerStore(tmp_path)
    store.put("tasks", "new", {"task_id": "new", "machine_id": "PC001"})
    manager = ManagerService(store=store)
    imported = []

    def import_memory(result: dict) -> dict:
        lock = RunLock(str(store.lock_path))
        assert lock.acquire(timeout=0.2)
        lock.release()
        imported.append(result["memory_records"])
        return {"success": True}

    monkeypatch.setattr(manager, "ingest_memory", import_memory)
    records = [{"layer": "runtime_fact", "content": "progress"}]
    with store.batch():
        manager.record_agent_message(
            {
                "task_id": "new",
                "from_agent": "PC001-AGENT",
                "to_agent": "MANAGER-AGENT-01",
                "message_type": "PROGRESS",
                "payload": {"memory_records": records},
            }
        )
        assert imported == []
    assert imported == [records]


def test_metadata_and_debug_updates_skip_live_receipt_loading(tmp_path, monkeypatch) -> None:
    from manager_service import ManagerService

    store = ManagerStore(tmp_path)
    store.put("machines", "PC001", {"machine_id": "PC001", "state": "READY"})
    store.put("tasks", "new", {"task_id": "new", "state": "SUCCESS"})
    store.record_result("receipt", {"task_id": "new", "state": "SUCCESS"})

    def fail_load() -> None:
        raise AssertionError("metadata mutation parsed every live receipt")

    monkeypatch.setattr(store.live, "load", fail_load)
    store.put("accounts", "PC001:a", {"state": "READY"})
    store.update("machines", "PC001", lambda row: {**row, "state": "PAUSED"})
    store.set_collection_cursor("PC001", 3)
    store.record_incremental("event", {"machine_id": "PC001"})
    manager = ManagerService(store=store)
    incident = manager.debug_action("report", {"machine_id": "PC001", "summary": "collection"})
    manager.debug_action("claim", {"incident_id": incident["incident"]["incident_id"]})
    assert store.get("tasks", "new")["state"] == "SUCCESS"
    assert store.get("results", "receipt")["state"] == "SUCCESS"
    assert store.get_collection_cursor("PC001") == 3


def test_idle_outbox_collection_does_not_load_live_rows_under_json_lock(
    tmp_path, monkeypatch
) -> None:
    from manager_service import ManagerService

    from tests.test_manager_outbox_collection import FileTransport

    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "index.json").write_text(
        json.dumps({"machine_id": "PC001", "records": [], "high_watermark": 0}), encoding="utf-8"
    )
    store = ManagerStore(tmp_path / "manager")
    store.put(
        "machines",
        "PC001",
        {
            "machine_id": "PC001",
            "task_outbox_root": str(outbox),
            "task_inbox_root": str(tmp_path / "inbox"),
        },
    )
    store.put("tasks", "new", {"task_id": "new"})
    original_load = store.live.load

    def assert_unlocked_load():
        assert not store.lock_path.exists()
        return original_load()

    monkeypatch.setattr(store.live, "load", assert_unlocked_load)
    result = ManagerService(store=store, transport=FileTransport()).collect_results(
        "PC001",
        download_evidence=False,
    )
    assert result["status"] == "IDLE"
    assert result["collected"] == 0


def test_account_sync_keeps_live_running_task_in_metadata_summary(tmp_path, monkeypatch) -> None:
    from manager_service import ManagerService

    class AccountTransport:
        def invoke(self, *args, **kwargs) -> dict:
            return {"success": True, "accounts": [{"name": "a", "status": "READY", "ready": True}]}

    store = ManagerStore(tmp_path)
    store.put("machines", "PC001", {"machine_id": "PC001"})
    store.put(
        "tasks",
        "live",
        {
            "task_id": "live",
            "machine_id": "PC001",
            "request": {"account_slot": "a"},
            "dispatch_state": "RUNNING",
        },
    )

    def fail_load() -> None:
        raise AssertionError("account metadata sync parsed every live receipt")

    monkeypatch.setattr(store.live, "load", fail_load)
    result = ManagerService(store=store, transport=AccountTransport()).list_machine_accounts(
        "PC001"
    )
    assert result["accounts"][0]["agent_task_id"] == "live"
    assert result["summary"]["RUNNING"] == 1
    assert store.get("machines", "PC001")["account_summary"]["RUNNING"] == 1
