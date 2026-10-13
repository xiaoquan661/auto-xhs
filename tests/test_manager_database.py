from __future__ import annotations

import pytest
from manager_database import ManagerDatabase
from operations_acceptance import acceptance_report, confirmed_counts
from service_errors import ServiceError
from worker_data_export import export_business_data


def test_projection_allows_state_writes_during_database_sync(tmp_path, monkeypatch):
    from manager_store import ManagerStore

    store = ManagerStore(tmp_path)
    store.put("settings", "sample", {"version": 1})
    database = ManagerDatabase(tmp_path)

    def sync(snapshot):
        # A different store instance represents a concurrent manager writer.
        ManagerStore(tmp_path).put("settings", "sample", {"version": 2})
        assert snapshot["settings"]["sample"]["version"] == 1
        return {"success": True}

    monkeypatch.setattr(database, "sync", sync)
    assert database.sync_store(store)["success"] is True
    assert store.get("settings", "sample")["version"] == 2


def test_collection_updates_current_receipts_preserving_history(tmp_path, monkeypatch):
    database = ManagerDatabase(tmp_path)
    database.sync({"results": {"historical": {"state": "SUCCESS"}}})

    def full_projection(*args):
        pytest.fail("即时采集应直接更新本次回执")

    monkeypatch.setattr(database, "sync", full_projection)
    receipt = {"record_id": "result:PC071:creator-ui-1:1", "machine_id": "PC071",
               "account_slot": "3-3", "state": "SUCCESS"}
    database.sync_collection_results([receipt])
    database.sync_collection_results([receipt])
    records = database.query("SELECT record_key FROM records WHERE collection='results'")
    assert {r["record_key"] for r in records["rows"]} == {"historical", "PC071:creator-ui-1:1"}


def test_projection_updates_only_changed_records(tmp_path):
    import sqlite3

    database = ManagerDatabase(tmp_path)
    database.sync({"results": {"one": {"state": "SUCCESS"}, "two": {"state": "FAILED"}}})
    with sqlite3.connect(database.path) as db:
        db.executescript("""
            CREATE TABLE changed_records (record_key TEXT);
            CREATE TRIGGER track_record_update AFTER UPDATE ON records
            BEGIN INSERT INTO changed_records VALUES (new.record_key); END;
        """)
    database.sync({"results": {"one": {"state": "SUCCESS"}, "two": {"state": "SUCCESS"}}})
    assert database.query("SELECT record_key FROM changed_records")["rows"] == [
        {"record_key": "two"},
    ]


def test_dashboard_reads_committed_metrics_while_import_is_writing(tmp_path):
    import json
    import sqlite3

    database = ManagerDatabase(tmp_path)
    database.sync({})
    metric = {"entity_type": "note", "entity_id": "n1", "views": 12}
    with sqlite3.connect(database.path) as seed:
        seed.execute("INSERT INTO worker_records VALUES (?,?,?,?,?)",
                     ("PC071", "metric_snapshots", "1", "3-3", json.dumps(metric)))
    writer = sqlite3.connect(database.path)
    try:
        writer.execute("BEGIN EXCLUSIVE")
        writer.execute("DELETE FROM worker_records")
        observed = database.employee_observations()
        assert len(observed) == 1
        assert observed[0]["data"]["views"] == 12
        writer.commit()
        assert database.employee_observations() == []
    finally:
        writer.close()


def test_worker_snapshot_all_tables_and_account_isolation(tmp_path):
    import sqlite3

    worker = tmp_path / "worker"
    worker.mkdir()
    with sqlite3.connect(worker / "operations.db") as connection:
        connection.executescript("CREATE TABLE events(account_slot TEXT, value TEXT);"
                                 "INSERT INTO events VALUES ('a', '完整字段');"
                                 "CREATE TABLE empty_table(optional_field TEXT);")
    snapshot = export_business_data(worker)
    assert "empty_table" in snapshot["schema"]
    db = ManagerDatabase(tmp_path)
    db.sync({})
    db.import_worker("PC052", snapshot)
    db.import_worker("PC057", snapshot)
    db.import_worker("PC057", snapshot)
    assert db.query("SELECT count(*) AS n FROM worker_records WHERE collection='events'")[
        "rows"] == [{"n": 2}]
    db.sync({"results": {}})
    assert db.query("SELECT count(*) AS n FROM worker_records WHERE collection='events'")[
        "rows"] == [{"n": 2}]


def test_full_fields_and_parameterized_query(tmp_path):
    db = ManagerDatabase(tmp_path)
    db.sync(
        {
            "results": {
                "r": {
                    "machine_id": "PC057",
                    "account_slot": "a",
                    "nested": {"items": [{"count": 2, "missing": None}]},
                }
            }
        }
    )
    result = db.query("SELECT value FROM fields WHERE path = ?", ["$.nested.items[0].count"])
    assert result["rows"] == [{"value": 2}]
    assert any(row["type"] == "null" for row in db.schema()["rows"])
    db.sync({"results": {}})
    assert db.query("SELECT * FROM records")["rows"] == []


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM records", "ATTACH DATABASE ':memory:' AS other", "PRAGMA writable_schema=ON"],
)
def test_query_is_readonly(tmp_path, sql):
    db = ManagerDatabase(tmp_path)
    db.sync({})
    with pytest.raises(ServiceError):
        db.query(sql)


def test_truncation(tmp_path):
    db = ManagerDatabase(tmp_path)
    db.sync({"a": {"one": 1, "two": 2}})
    result = db.query("SELECT * FROM records", limit=1)
    assert result["truncated"] and len(result["rows"]) == 1


def test_status_alone_does_not_prove_write():
    assert not confirmed_counts({"state": "SUCCESS"}, {"capability": "post-comment"})["comment"]
    assert not confirmed_counts(
        {"state": "RESULT_UNKNOWN", "item_results": [{"success": True, "platform_readback": True}]},
        {"capability": "post-comment"},
    )["comment"]


def test_follow_must_change():
    receipt = {
        "state": "SUCCESS",
        "item_results": [{"success": True, "platform_readback": True, "changed": False}],
    }
    assert confirmed_counts(receipt, {"capability": "follow-user"})["friend_request"] == 0
    receipt["item_results"][0]["changed"] = True
    assert confirmed_counts(receipt, {"capability": "follow-user"})["friend_request"] == 1


def test_account_scope_and_revision():
    result = {
        "task_id": "t",
        "machine_id": "PC057",
        "account_slot": "a",
        "remote_execution_id": "e",
        "state": "SUCCESS",
        "result_revision": 1,
        "completed_at": "2026-09-10T01:00:00Z",
        "item_results": [{"success": True, "platform_readback": True}],
    }
    state = {
        "tasks": {"t": {"request": {"capability": "post-comment"}}},
        "results": {"r1": result, "r2": {**result, "result_revision": 2}},
    }
    report = acceptance_report(state, day="2026-09-10", accounts=["PC057:a"])
    comment = next(r for r in report["actions"] if r["action"] == "comment")
    assert comment["actual"] == 1 and not report["passed"]
    assert all(r["actual"] == 0 for r in acceptance_report(state, accounts=["PC052:a"])["actions"])


def test_private_message_reply_and_historical_rule_are_account_scoped(tmp_path):
    import json
    import sqlite3

    db = ManagerDatabase(tmp_path)
    db.sync({})
    with sqlite3.connect(db.path) as connection:
        rows = [
            ("inbound_events", "event", {"event_id": "event", "event_type": "private_message",
                                         "payload_json": '{"content":"收到原文"}'}),
            ("reply_records", "reply", {"inbound_event_id": "event", "reply_text": "实际回复",
                                         "rule_id": "rule", "rule_version": 2}),
            ("reply_rule_versions", "rule2", {"rule_id": "rule", "rule_version": 2,
                                                "reply_style": "historical"}),
            ("reply_rules", "rule", {"rule_id": "rule", "rule_version": 3,
                                       "reply_style": "current"}),
        ]
        for collection, key, payload in rows:
            connection.execute("INSERT INTO worker_records VALUES (?, ?, ?, ?, ?)",
                               ("PC1", collection, key, "a", json.dumps(payload)))
        connection.execute("INSERT INTO worker_records VALUES (?, ?, ?, ?, ?)",
                           ("PC2", "reply_records", "other", "a", json.dumps(
                               {"inbound_event_id": "event", "reply_text": "另一机器"})))
    event = db.employee_observations()[0]["data"]
    assert event["payload"]["content"] == "收到原文"
    assert len(event["reply_history"]) == 1
    assert event["reply_history"][0]["reply_text"] == "实际回复"
    assert event["processing_rule"]["reply_style"] == "historical"
    assert event["processing_rule_basis"] == "applied"
