from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import manager_store
from manager_store import ManagerStore
from run_lock import RunLock


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
    writer.put("machines", "PC001", {"state": "READY"})
    decodes = 0
    original_loads = manager_store.orjson.loads

    def count_loads(value: bytes) -> dict:
        nonlocal decodes
        decodes += 1
        return original_loads(value)

    monkeypatch.setattr(manager_store.orjson, "loads", count_loads)
    assert reader.get("machines", "PC001")["state"] == "READY"
    assert reader.get("machines", "PC001")["state"] == "READY"
    assert decodes == 1

    writer.put("machines", "PC001", {"state": "PAUSED"})
    assert reader.get("machines", "PC001")["state"] == "PAUSED"
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
    store.put("machines", "PC001", {"state": "READY"})

    assert attempts == 3
    assert store.get("machines", "PC001")["state"] == "READY"


def test_batch_prepares_json_before_acquiring_file_lock(tmp_path: Path, monkeypatch) -> None:
    store = ManagerStore(tmp_path)
    store.put("machines", "PC001", {"state": "READY"})
    original_read = store._read_state_fresh

    def unlocked_read() -> dict:
        probe = RunLock(str(store.lock_path), poll_interval_seconds=0.01)
        assert probe.acquire(timeout=0.2)
        probe.release()
        return original_read()

    monkeypatch.setattr(store, "_read_state_fresh", unlocked_read)
    with store.batch(include_live=False):
        assert store.lock_path.exists()
        store.update("machines", "PC001", lambda row: {**row, "state": "PAUSED"})
    assert store.get("machines", "PC001")["state"] == "PAUSED"


def test_batch_reprepares_after_another_process_updates_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    store = ManagerStore(tmp_path)
    store.put("machines", "PC001", {"state": "READY"})
    original_read = store._read_state_fresh
    reads = 0
    callback_calls = 0
    writer_code = """
import sys
sys.path.insert(0, sys.argv[1])
from manager_store import ManagerStore
ManagerStore(sys.argv[2]).put("machines", "PC002", {"state": "READY"})
"""

    def read_with_concurrent_update() -> dict:
        nonlocal reads
        prepared = original_read()
        reads += 1
        if reads == 1:
            assert not store.lock_path.exists()
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    writer_code,
                    str(Path(manager_store.__file__).parent),
                    str(tmp_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        return prepared

    def update(state: dict) -> None:
        nonlocal callback_calls
        callback_calls += 1
        assert store.lock_path.exists()
        state["machines"]["PC001"]["state"] = "PAUSED"

    monkeypatch.setattr(store, "_read_state_fresh", read_with_concurrent_update)
    store.mutate(update, include_live=False)
    assert reads == 2
    assert callback_calls == 1
    saved = ManagerStore(tmp_path)
    assert saved.get("machines", "PC001")["state"] == "PAUSED"
    assert saved.get("machines", "PC002")["state"] == "READY"
