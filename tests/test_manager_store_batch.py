from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

import manager_store
from manager_store import ManagerStore


def test_batch_commits_related_updates_once(tmp_path: Path, monkeypatch) -> None:
    store = ManagerStore(tmp_path)
    writes = 0
    original_write = store._write_state

    def count_write(state: dict) -> None:
        nonlocal writes
        writes += 1
        original_write(state)

    monkeypatch.setattr(store, "_write_state", count_write)
    with store.batch():
        store.put("tasks", "task-1", {"task_id": "task-1", "state": "ACCEPTED"})
        with store.batch():
            store.record_result("result-1", {"task_id": "task-1", "state": "SUCCESS"})
        store.set_collection_cursor("PC071:sftp_outbox", 4)

    assert writes == 1
    restored = ManagerStore(tmp_path)
    assert restored.get("tasks", "task-1")["state"] == "ACCEPTED"
    assert restored.list("results")[0]["state"] == "SUCCESS"
    assert restored.get_collection_cursor("PC071:sftp_outbox") == 4


def test_reads_use_committed_state_during_background_batch(tmp_path: Path) -> None:
    store = ManagerStore(tmp_path)
    store.put("tasks", "task-1", {"state": "ACCEPTED"})
    started = Event()
    release = Event()

    def update_in_background() -> None:
        with store.batch():
            store.put("tasks", "task-1", {"state": "SUCCESS"})
            started.set()
            release.wait(2)

    worker = Thread(target=update_in_background)
    worker.start()
    try:
        assert started.wait(2)
        assert store.get("tasks", "task-1")["state"] == "ACCEPTED"
    finally:
        release.set()
        worker.join(2)
    assert store.get("tasks", "task-1")["state"] == "SUCCESS"


def test_read_cache_refreshes_after_another_store_commits(tmp_path: Path, monkeypatch) -> None:
    writer = ManagerStore(tmp_path)
    reader = ManagerStore(tmp_path)
    writer.put("tasks", "task-1", {"state": "ACCEPTED"})
    decodes = 0
    original_loads = manager_store.orjson.loads

    def count_loads(value: bytes) -> dict:
        nonlocal decodes
        decodes += 1
        return original_loads(value)

    monkeypatch.setattr(manager_store.orjson, "loads", count_loads)
    assert reader.get("tasks", "task-1")["state"] == "ACCEPTED"
    assert reader.get("tasks", "task-1")["state"] == "ACCEPTED"
    assert decodes == 1

    writer.put("tasks", "task-1", {"state": "SUCCESS"})
    assert reader.get("tasks", "task-1")["state"] == "SUCCESS"
    assert decodes == 3  # writer's fresh transaction read and reader's refreshed read


def test_state_commit_waits_for_temporary_file_access(tmp_path: Path, monkeypatch) -> None:
    store = ManagerStore(tmp_path)
    replace = manager_store.os.replace
    attempts = 0

    def temporarily_busy(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("state file is being read")
        replace(source, destination)

    monkeypatch.setattr(manager_store.os, "replace", temporarily_busy)
    monkeypatch.setattr(manager_store.time, "sleep", lambda _: None)
    store.put("tasks", "task-1", {"state": "ACCEPTED"})

    assert attempts == 3
    assert store.get("tasks", "task-1")["state"] == "ACCEPTED"
