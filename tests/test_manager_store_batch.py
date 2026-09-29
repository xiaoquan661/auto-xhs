from __future__ import annotations

from pathlib import Path
from threading import Event, Thread

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
