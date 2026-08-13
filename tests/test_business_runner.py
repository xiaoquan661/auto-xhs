from __future__ import annotations

import threading
import time

from scripts.business_runner import BusinessRunner


def test_runner_serializes_same_account(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    active = 0
    maximum = 0
    guard = threading.Lock()

    def executor(account, capability, parameters):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return {"account": account, "capability": capability, **parameters}

    runner = BusinessRunner(executor)
    threads = [
        threading.Thread(target=runner.execute, args=("alpha", "search-feeds", {"i": i}))
        for i in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum == 1


def test_runner_allows_different_accounts_in_parallel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    active = 0
    maximum = 0
    guard = threading.Lock()

    def executor(_account, _capability, _parameters):
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return {"success": True}

    runner = BusinessRunner(executor)
    threads = [
        threading.Thread(target=runner.execute, args=(name, "search-feeds", {}))
        for name in ("alpha", "beta")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert maximum == 2
