from __future__ import annotations

from pathlib import Path

from manager_service import ManagerService
from manager_store import ManagerStore


def test_publish_policy_uses_latest_account_result(tmp_path: Path) -> None:
    store = ManagerStore(tmp_path)
    service = ManagerService(store=store)
    original = {
        "machine_id": "PC071",
        "account_slot": "3-2",
        "capability": "publish",
        "remote_execution_id": "execution-1",
        "state": "RESULT_UNKNOWN",
        "result_revision": 1,
    }
    store.record_result("original", original)
    store.record_result("revision", {**original, "state": "BLOCKED", "result_revision": 2})
    store.record_result(
        "other-account", {**original, "account_slot": "3-3", "remote_execution_id": "execution-2"}
    )

    history = service._publish_policy_results("PC071", "3-2")

    assert len(history) == 1
    assert history[0]["state"] == "BLOCKED"
