from manager_store import ManagerStore


def test_display_projection_preserves_persisted_details_and_counts(tmp_path):
    store = ManagerStore(tmp_path)
    row = {"task_id": "one", "request": {"parameters": {
        "memory_context": {"content": "large history"}, "count": 2,
    }}, "item_results": [{"success": True}], "memory_usages": [{"id": "memory-1"}]}
    store.put("tasks", "one", row)
    view = store.read_view(excluded_fields=frozenset({"memory_context", "memory_usages"}))
    displayed = view.get("tasks", "one")
    assert displayed["request"]["parameters"] == {"count": 2}
    assert displayed["item_results"] == [{"success": True}]
    assert "memory_usages" not in displayed
    assert store.get("tasks", "one") == row
