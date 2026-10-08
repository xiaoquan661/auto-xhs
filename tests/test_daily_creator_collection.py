from daily_creator_collection import envelope, local_published_note_ids
from daily_passive_collection import eligibility


def test_daily_task_identity_and_collection_contract():
    first = envelope("PC052", "2-2", "xiaohongshu", "2026-09-24")
    assert first == envelope("PC052", "2-2", "xiaohongshu", "2026-09-24")
    assert first["task_id"] != envelope("PC052", "2-2", "xiaohongshu", "2026-09-25")["task_id"]
    task = first["task"]
    assert task["write_intent"] is False
    assert task["parameters"]["source"] == "creator_manager"
    assert task["parameters"]["scheduled_at"] == "2026-09-24T00:30:00+08:00"


def test_paused_account_is_not_queued():
    state = {"machine": {}, "account_management": {"xiix": {"paused": True}}, "executions": {}}
    assert (
        eligibility(state, {"settings": {}}, "xiix", {"accounts": {"xiix": {"enabled": True}}})
        == "PAUSED"
    )


def test_collection_start_date_is_account_scoped():
    request = envelope("PC071", "3-3", "xiaohongshu", "2026-09-25", published_since="2026-09-22")
    assert request["task"]["parameters"]["published_since"] == "2026-09-22"
    default_request = envelope("PC071", "3-2", "xiaohongshu", "2026-09-25")
    assert "published_since" not in default_request["task"]["parameters"]


def test_local_publish_scope_uses_successful_receipt_note_ids():
    results = [
        {
            "account_slot": "3-3",
            "state": "SUCCESS",
            "capability": "publish",
            "business_result": {"published": 1},
            "item_results": [{"status": "SUCCESS", "note_id": "local-1"}],
        },
        {
            "account_slot": "3-3",
            "state": "SUCCESS",
            "capability": "publish",
            "business_result": {"published": 1, "note_id": "local-2"},
        },
        {
            "account_slot": "3-2",
            "state": "SUCCESS",
            "capability": "publish",
            "business_result": {"published": 1, "note_id": "other-slot"},
        },
        {
            "account_slot": "3-3",
            "state": "RESULT_UNKNOWN",
            "capability": "publish",
            "business_result": {"note_id": "unconfirmed"},
        },
    ]
    note_ids = local_published_note_ids(results, "3-3")
    assert note_ids == ["local-1", "local-2"]
    request = envelope(
        "PC071",
        "3-3",
        "rednote",
        "2026-09-29",
        published_since="2026-09-22",
        local_note_ids=note_ids,
    )
    parameters = request["task"]["parameters"]
    assert parameters["note_ids"] == note_ids
    assert parameters["local_published_only"] is True
    assert "仅采集本机本槽位成功发布回执" in parameters["instructions"]
