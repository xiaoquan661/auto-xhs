from __future__ import annotations

import json
from pathlib import Path

import pytest
from employee_codex_runner import EmployeeCodexRunner
from manager_worker_service import WorkerServiceError
from run_lock import RunLock


@pytest.mark.parametrize("error", [PermissionError("lock denied"),
                                  WorkerServiceError("STATE_BUSY", "繁忙")])
def test_state_error_keeps_agent_and_next_stop_works(tmp_path: Path, error: Exception) -> None:
    calls = iter([error, True])

    def check() -> bool:
        value = next(calls)
        if isinstance(value, Exception):
            raise value
        return value

    runner = EmployeeCodexRunner(tmp_path, should_stop=check)
    assert runner._poll_stop(tmp_path) is False
    assert runner._poll_stop(tmp_path) is True
    logged = json.loads((tmp_path / "state-check-errors.jsonl").read_text(encoding="utf-8"))
    assert logged["stage"] == "stop_check"


def test_empty_lock_is_not_stale(tmp_path: Path) -> None:
    lock_file = tmp_path / "lock"
    lock_file.touch()
    assert RunLock(str(lock_file))._is_stale() is False


def test_unreadable_lock_is_not_stale(tmp_path: Path, monkeypatch) -> None:
    def denied(*args, **kwargs):
        raise PermissionError("sharing")
    monkeypatch.setattr("builtins.open", denied)
    assert RunLock(str(tmp_path / "lock"))._is_stale() is False


def test_temporary_open_denial_recovers(tmp_path: Path, monkeypatch) -> None:
    import run_lock
    original = run_lock.os.open
    attempts = []

    def open_once(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise PermissionError("sharing")
        return original(*args, **kwargs)

    monkeypatch.setattr(run_lock.os, "open", open_once)
    lock = RunLock(str(tmp_path / "lock"))
    assert lock.acquire(timeout=1)
    lock.release()
