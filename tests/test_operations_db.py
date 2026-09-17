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


def test_version_twelve_rules_keep_history_after_migration(tmp_path) -> None:
    from scripts.reply_rule_service import ReplyRuleService

    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 13):
            connection.executescript(_MIGRATIONS[version])
        connection.execute(
            """
            INSERT INTO reply_rules (
                rule_id, account_slot, enabled, hourly_limit, daily_limit,
                auto_categories_json, created_at, updated_at
            ) VALUES ('existing', 'alpha', 1, 1, 2, '["ordinary"]', '2026-09-15', '2026-09-15')
            """
        )
        connection.execute(
            """
            INSERT INTO reply_rule_versions SELECT
                rule_id, rule_version, account_slot, platform, enabled,
                content_scope, reply_style, active_time_range, hourly_limit, daily_limit,
                comment_enabled, private_message_enabled, auto_categories_json,
                manual_categories_json, account_profile, knowledge, created_at, updated_at
            FROM reply_rules
            """
        )
        connection.execute("PRAGMA user_version = 12")

    database = OperationsDatabase(path=path)
    service = ReplyRuleService(database)
    for table in ("reply_rules", "reply_rule_versions"):
        columns = {row["name"] for row in database.fetch_all(f"PRAGMA table_info({table})")}
        assert "hourly_limit" not in columns
        assert "daily_limit" not in columns
    assert service.get_version("existing", 1)["enabled"] is True
    assert service.decision("existing")["authorized"] is True
    updated = service.update("existing", reply_style="professional")
    assert updated["rule_version"] == 2
    assert service.get_version("existing", 1)["reply_style"] == "natural"
    assert (
        OperationsDatabase(path=path).fetch_one("PRAGMA user_version")["user_version"]
        == SCHEMA_VERSION
    )


def test_agent_reply_migration_restores_unsent_events_and_keeps_send_evidence(tmp_path) -> None:
    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 14):
            connection.executescript(_MIGRATIONS[version])
        for event_id, task_id in (("pending", None), ("followup", None), ("sent", "task-1")):
            connection.execute(
                """
                INSERT INTO inbound_events (
                    event_id, account_slot, event_type, platform_event_id,
                    occurred_at, discovered_at, handling_state, created_task_id, payload_json
                ) VALUES (?, 'alpha', 'private_message', ?, '2026-09-15', '2026-09-15',
                          'HANDLED', ?, '{"conversation_owner":"HUMAN"}')
                """,
                (event_id, event_id, task_id),
            )
        connection.execute(
            """
            INSERT INTO reply_decisions (
                decision_id, cycle_id, inbound_event_id, account_slot, channel,
                decision, reason, created_at, updated_at
            ) VALUES ('d1', 'c1', 'pending', 'alpha', 'private_message',
                      'pending', 'manual_category', '2026-09-15', '2026-09-15')
            """
        )
        connection.execute(
            """
            INSERT INTO private_message_conversations (
                account_slot, user_id, owner_state, updated_at
            ) VALUES ('alpha', 'peer', 'HUMAN', '2026-09-15')
            """
        )
        connection.execute("PRAGMA user_version = 13")

    database = OperationsDatabase(path=path)
    states = {row["event_id"]: row for row in database.fetch_all("SELECT * FROM inbound_events")}
    assert states["pending"]["handling_state"] == "NEW"
    assert states["followup"]["handling_state"] == "NEW"
    assert states["sent"]["created_task_id"] == "task-1"
    assert states["sent"]["handling_state"] == "HANDLED"
    assert (
        database.fetch_one("SELECT * FROM private_message_conversations")["owner_state"] == "CODEX"
    )
    for table in ("reply_rules", "reply_rule_versions"):
        columns = {row["name"] for row in database.fetch_all(f"PRAGMA table_info({table})")}
        assert "manual_categories_json" not in columns
        assert "auto_categories_json" not in columns


def test_existing_reply_schedule_migrates_to_agent_rule(tmp_path) -> None:
    from scripts.reply_rule_service import ReplyRuleService

    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 15):
            connection.executescript(_MIGRATIONS[version])
        connection.execute(
            """
            INSERT INTO reply_rules (
                rule_id, account_slot, enabled, active_time_range, created_at, updated_at
            ) VALUES ('scheduled', 'alpha', 1, '09:00-10:00', '2026-09-15', '2026-09-15')
            """
        )
        connection.execute("PRAGMA user_version = 14")
    database = OperationsDatabase(path=path)
    service = ReplyRuleService(database)
    assert service.decision("scheduled")["authorized"] is True
    assert service.update("scheduled", reply_style="natural")["rule_version"] == 2
    for table in ("reply_rules", "reply_rule_versions"):
        columns = {row["name"] for row in database.fetch_all(f"PRAGMA table_info({table})")}
        assert "active_time_range" not in columns


def test_platform_event_migration_preserves_existing_comment_links(tmp_path):
    from scripts.inbound_event_service import InboundEventService

    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as connection:
        for version in range(1, 16):
            connection.executescript(_MIGRATIONS[version])
        connection.execute("PRAGMA user_version = 15")
        connection.execute("INSERT INTO inbound_events "
                           "(event_id,account_slot,event_type,platform_event_id,occurred_at,"
                           "discovered_at,handling_state,created_task_id) "
                           "VALUES ('old','a','note_comment','same','2026-09-15','2026-09-15',"
                           "'HANDLED','reply-1')")
        connection.execute("INSERT INTO comment_collection_batches "
                           "(batch_id,account_slot,platform,started_at) "
                           "VALUES ('batch','a','xiaohongshu','2026-09-15')")
        connection.execute("INSERT INTO comment_batch_events (batch_id,event_id) "
                           "VALUES ('batch','old')")
    database = OperationsDatabase(path=path)
    service = InboundEventService(database)
    values = dict(account_slot="a", platform="rednote", event_type="note_comment",
                  platform_event_id="same", occurred_at="2026-09-15T10:00:00Z")
    new = service.record(**values)
    repeated = service.record(**values)
    assert new["created"] is True
    assert repeated["created"] is False
    assert repeated["event"]["event_id"] == new["event"]["event_id"]
    assert service.get("old")["handling_state"] == "HANDLED"
    assert service.get("old")["created_task_id"] == "reply-1"
    assert database.fetch_one("SELECT event_id FROM comment_batch_events")["event_id"] == "old"
    assert database.fetch_all("PRAGMA foreign_key_check") == []
