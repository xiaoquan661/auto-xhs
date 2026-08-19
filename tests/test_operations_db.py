from __future__ import annotations

from scripts.operations_db import OperationsDatabase, SCHEMA_VERSION


def test_database_initializes_and_reopens(tmp_path) -> None:
    path = tmp_path / "operations.db"
    database = OperationsDatabase(path=path)

    assert path.exists()
    assert database.fetch_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION

    reopened = OperationsDatabase(path=path)
    tables = {
        row["name"]
        for row in reopened.fetch_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {
        "inbound_events",
        "collector_cursors",
        "operation_events",
        "reply_rules",
        "metric_snapshots",
        "action_drafts",
        "action_approvals",
    } <= tables
