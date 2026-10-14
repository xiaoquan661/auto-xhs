from __future__ import annotations

import os
import time
from pathlib import Path
from threading import Thread

from scripts import run_lock
from scripts.run_lock import RunLock


def test_release_retries_transient_file_sharing_conflict(
    tmp_path: Path,
    monkeypatch,
) -> None:
    lock_path = tmp_path / "product-state.lock"
    lock = RunLock(str(lock_path))
    assert lock.acquire(timeout=0.1)

    real_remove = os.remove
    remove_attempts = 0
    sleep_calls: list[float] = []

    def remove_with_transient_conflict(path: str) -> None:
        nonlocal remove_attempts
        remove_attempts += 1
        if remove_attempts == 1:
            raise PermissionError(13, "文件正被另一个进程使用", path)
        real_remove(path)

    monkeypatch.setattr(run_lock.os, "remove", remove_with_transient_conflict)
    monkeypatch.setattr(run_lock.time, "sleep", sleep_calls.append)

    lock.release()

    assert remove_attempts == 2
    assert sleep_calls == [run_lock._RELEASE_RETRY_DELAY_SECONDS]
    assert not lock_path.exists()


def test_acquire_recovers_old_empty_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "product-state.lock"
    lock_path.touch()
    old = time.time() - run_lock._INCOMPLETE_LOCK_STALE_SECONDS - 1
    os.utime(lock_path, (old, old))

    lock = RunLock(str(lock_path))
    assert lock.acquire(timeout=1)
    try:
        assert lock_path.read_text(encoding="utf-8") == str(os.getpid())
    finally:
        lock.release()


def test_short_poll_interval_acquires_after_competing_writer_releases(tmp_path: Path) -> None:
    lock_path = str(tmp_path / "manager-state.lock")
    writer = RunLock(lock_path)
    assert writer.acquire(timeout=0.1)

    def release_writer() -> None:
        time.sleep(0.15)
        writer.release()

    thread = Thread(target=release_writer)
    thread.start()
    reader = RunLock(lock_path, poll_interval_seconds=0.05)
    try:
        assert reader.acquire(timeout=0.8)
    finally:
        reader.release()
        thread.join(timeout=1)
