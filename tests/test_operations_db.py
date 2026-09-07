from __future__ import annotations

import sqlite3

from scripts.operations_db import _MIGRATIONS, SCHEMA_VERSION, OperationsDatabase


def test_database_initializes_and_reopens(tmp_path) -> None:
    path = tmp_path / "operations.db"
    database = OperationsDatabase(path=path)

    assert path.exists()
    assert database.fetch_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION

    reopened = OperationsDatabase(path=path)
    tables = {
        row["name"]
        for row in reopened.fetch_all("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "inbound_events",
        "collector_cursors",
        "operation_events",
        "reply_rules",
        "reply_rule_versions",
        "metric_snapshots",
        "action_drafts",
        "action_approvals",
        "published_posts",
        "reply_records",
        "reply_cycles",
        "reply_decisions",
        "private_message_conversations",
        "operation_reports",
    } <= tables


def test_database_migrates_existing_version_five_to_conversation_ownership(tmp_path) -> None:
    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 6):
            connection.executescript(_MIGRATIONS[version])
        connection.execute("PRAGMA user_version = 5")

    migrated = OperationsDatabase(path=path)

    assert migrated.fetch_one("PRAGMA user_version")["user_version"] == SCHEMA_VERSION
    assert (
        migrated.fetch_one(
            """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'private_message_conversations'
        """
        )["name"]
        == "private_message_conversations"
    )
    assert (
        migrated.fetch_one(
            """
        SELECT name FROM sqlite_master
        WHERE type = 'table' AND name = 'reply_rule_versions'
        """
        )["name"]
        == "reply_rule_versions"
    )


def test_database_migrates_existing_rule_to_version_history(tmp_path) -> None:
    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 8):
            connection.executescript(_MIGRATIONS[version])
        connection.execute(
            """
            INSERT INTO reply_rules (
                rule_id, account_slot, platform, enabled, content_scope,
                reply_style, active_time_range, hourly_limit, daily_limit,
                comment_enabled, private_message_enabled, auto_categories_json,
                manual_categories_json, account_profile, knowledge,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "rule-existing",
                "alpha",
                "xiaohongshu",
                1,
                "all_own_notes",
                "natural",
                "",
                0,
                0,
                1,
                1,
                '["ordinary"]',
                '["complaint"]',
                "账号人设",
                "确认知识",
                "2026-08-28T08:00:00+00:00",
                "2026-08-28T09:00:00+00:00",
            ),
        )
        connection.execute("PRAGMA user_version = 7")

    migrated = OperationsDatabase(path=path)

    current = migrated.fetch_one("SELECT * FROM reply_rules WHERE rule_id = 'rule-existing'")
    historical = migrated.fetch_one(
        """
        SELECT * FROM reply_rule_versions
        WHERE rule_id = 'rule-existing' AND rule_version = 1
        """
    )
    assert current["rule_version"] == 1
    assert historical["reply_style"] == "natural"
    assert historical["knowledge"] == "确认知识"
