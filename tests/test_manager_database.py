from __future__ import annotations

import pytest
from manager_database import ManagerDatabase
from operations_acceptance import acceptance_report, confirmed_counts
from service_errors import ServiceError
from worker_data_export import export_business_data


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
