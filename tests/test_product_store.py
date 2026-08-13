from __future__ import annotations

import json

from scripts.product_store import ProductStore


def test_store_persists_settings_and_records(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")

    assert store.get_setting("global_paused") is False
    store.set_setting("global_paused", True)
    store.put("events", "event-1", {"event_id": "event-1", "state": "SUCCESS"})

    reloaded = ProductStore(tmp_path / "product")
    assert reloaded.get_setting("global_paused") is True
    assert reloaded.get("events", "event-1")["state"] == "SUCCESS"


def test_store_returns_copies_instead_of_live_state(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    original = {"task_id": "task-1", "state": "QUEUED"}
    store.put("tasks", "task-1", original)

    original["state"] = "FAILED"
    loaded = store.get("tasks", "task-1")
    loaded["state"] = "RUNNING"

    assert store.get("tasks", "task-1")["state"] == "QUEUED"


def test_store_writes_complete_json_and_removes_lock(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    store.set_setting("global_concurrency", 2)

    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["settings"]["global_concurrency"] == 2
    assert not store.lock_path.exists()


def test_store_multi_collection_mutation_is_atomic(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")

    def mutate(state):
        state["drafts"]["draft-1"] = {"draft_id": "draft-1"}
        state["approvals"]["approval-1"] = {"approval_id": "approval-1"}
        return "done"

    assert store.mutate(mutate) == "done"
    snapshot = store.snapshot()
    assert "draft-1" in snapshot["drafts"]
    assert "approval-1" in snapshot["approvals"]
