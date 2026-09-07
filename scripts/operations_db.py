"""SQLite storage for inbound platform events and long-running operations data."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from product_store import product_root

SCHEMA_VERSION = 9

_MIGRATIONS = {
    1: """
        CREATE TABLE inbound_events (
            event_id TEXT PRIMARY KEY,
            account_slot TEXT NOT NULL,
            event_type TEXT NOT NULL,
            platform_event_id TEXT NOT NULL,
            object_type TEXT NOT NULL DEFAULT '',
            object_id TEXT NOT NULL DEFAULT '',
            actor_user_id TEXT NOT NULL DEFAULT '',
            occurred_at TEXT NOT NULL,
            discovered_at TEXT NOT NULL,
            payload_json TEXT NOT NULL DEFAULT '{}',
            classification TEXT NOT NULL DEFAULT '',
            handling_state TEXT NOT NULL DEFAULT 'NEW',
            created_task_id TEXT,
            UNIQUE (account_slot, event_type, platform_event_id)
        );

        CREATE INDEX inbound_events_account_state_idx
            ON inbound_events (account_slot, handling_state, occurred_at DESC);

        CREATE TABLE collector_cursors (
            account_slot TEXT NOT NULL,
            collector_type TEXT NOT NULL,
            cursor_value TEXT NOT NULL DEFAULT '',
            last_seen_time TEXT,
            last_success_time TEXT,
            last_attempt_time TEXT NOT NULL,
            last_error TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (account_slot, collector_type)
        );

        CREATE TABLE operation_events (
            operation_event_id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL,
            account_slot TEXT NOT NULL,
            capability TEXT NOT NULL,
            target_type TEXT NOT NULL DEFAULT '',
            target_id TEXT NOT NULL DEFAULT '',
            started_at TEXT,
            finished_at TEXT,
            result_state TEXT NOT NULL,
            platform_result_json TEXT NOT NULL DEFAULT '{}',
            readback_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX operation_events_task_idx
            ON operation_events (task_id, finished_at DESC);

        CREATE TABLE reply_rules (
            rule_id TEXT PRIMARY KEY,
            account_slot TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 0,
            content_scope TEXT NOT NULL DEFAULT 'all_own_notes',
            reply_style TEXT NOT NULL DEFAULT 'natural',
            active_time_range TEXT NOT NULL DEFAULT '',
            hourly_limit INTEGER NOT NULL DEFAULT 0,
            daily_limit INTEGER NOT NULL DEFAULT 0,
            manual_categories_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX reply_rules_account_idx
            ON reply_rules (account_slot, enabled);

        CREATE TABLE metric_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            account_slot TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            captured_at TEXT NOT NULL,
            source TEXT NOT NULL,
            likes INTEGER,
            favorites INTEGER,
            comments INTEGER,
            shares INTEGER,
            followers INTEGER,
            following INTEGER,
            views INTEGER,
            impressions INTEGER,
            notes INTEGER,
            extra_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE (account_slot, entity_type, entity_id, captured_at, source)
        );

        CREATE INDEX metric_snapshots_entity_time_idx
            ON metric_snapshots (
                account_slot,
                entity_type,
                entity_id,
                captured_at DESC
            );
    """,
    2: """
        CREATE TABLE action_drafts (
            draft_id TEXT PRIMARY KEY,
            revision_id TEXT NOT NULL,
            account_slot TEXT NOT NULL,
            verified_uid TEXT NOT NULL,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            preview_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'DRAFT',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX action_drafts_account_state_idx
            ON action_drafts (account_slot, state, updated_at DESC);

        CREATE TABLE action_approvals (
            approval_id TEXT PRIMARY KEY,
            draft_id TEXT NOT NULL,
            revision_id TEXT NOT NULL,
            state TEXT NOT NULL,
            confirmed_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            consumed_at TEXT,
            FOREIGN KEY (draft_id) REFERENCES action_drafts(draft_id)
        );

        CREATE INDEX action_approvals_draft_idx
            ON action_approvals (draft_id, confirmed_at DESC);
    """,
    3: """
        ALTER TABLE inbound_events
            ADD COLUMN platform TEXT NOT NULL DEFAULT 'xiaohongshu';
        ALTER TABLE collector_cursors
            ADD COLUMN platform TEXT NOT NULL DEFAULT 'xiaohongshu';
        ALTER TABLE operation_events
            ADD COLUMN platform TEXT NOT NULL DEFAULT 'xiaohongshu';
        ALTER TABLE reply_rules
            ADD COLUMN platform TEXT NOT NULL DEFAULT 'xiaohongshu';
        ALTER TABLE metric_snapshots
            ADD COLUMN platform TEXT NOT NULL DEFAULT 'xiaohongshu';
        ALTER TABLE action_drafts
            ADD COLUMN platform TEXT NOT NULL DEFAULT 'xiaohongshu';
    """,
    4: """
        ALTER TABLE operation_events
            ADD COLUMN action TEXT NOT NULL DEFAULT '';
        ALTER TABLE operation_events
            ADD COLUMN result_summary TEXT NOT NULL DEFAULT '';
        ALTER TABLE operation_events
            ADD COLUMN error_code TEXT NOT NULL DEFAULT '';
        ALTER TABLE operation_events
            ADD COLUMN recommended_action TEXT NOT NULL DEFAULT '';

        CREATE TABLE published_posts (
            published_post_id TEXT PRIMARY KEY,
            publish_task_id TEXT NOT NULL UNIQUE,
            operation_event_id TEXT NOT NULL DEFAULT '',
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'xiaohongshu',
            verified_uid TEXT NOT NULL DEFAULT '',
            draft_id TEXT NOT NULL DEFAULT '',
            draft_revision TEXT NOT NULL DEFAULT '',
            source_content_id TEXT NOT NULL DEFAULT '',
            platform_post_id TEXT NOT NULL DEFAULT '',
            stable_url TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT '',
            published_at TEXT,
            result_state TEXT NOT NULL,
            readback_state TEXT NOT NULL DEFAULT 'MISSING',
            error_code TEXT NOT NULL DEFAULT '',
            result_summary TEXT NOT NULL DEFAULT '',
            platform_result_json TEXT NOT NULL DEFAULT '{}',
            readback_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX published_posts_account_time_idx
            ON published_posts (account_slot, published_at DESC, updated_at DESC);

        CREATE TABLE reply_records (
            reply_record_id TEXT PRIMARY KEY,
            channel TEXT NOT NULL,
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'xiaohongshu',
            verified_uid TEXT NOT NULL DEFAULT '',
            inbound_event_id TEXT NOT NULL DEFAULT '',
            conversation_id TEXT NOT NULL DEFAULT '',
            thread_id TEXT NOT NULL DEFAULT '',
            original_message_id TEXT NOT NULL DEFAULT '',
            original_content TEXT NOT NULL DEFAULT '',
            target_user_id TEXT NOT NULL DEFAULT '',
            target_user_name TEXT NOT NULL DEFAULT '',
            reply_text TEXT NOT NULL,
            draft_id TEXT NOT NULL DEFAULT '',
            draft_revision TEXT NOT NULL DEFAULT '',
            approval_id TEXT NOT NULL DEFAULT '',
            reply_task_id TEXT NOT NULL UNIQUE,
            operation_event_id TEXT NOT NULL DEFAULT '',
            sent_at TEXT,
            result_state TEXT NOT NULL,
            error_code TEXT NOT NULL DEFAULT '',
            result_summary TEXT NOT NULL DEFAULT '',
            readback_state TEXT NOT NULL DEFAULT 'MISSING',
            readback_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX reply_records_account_time_idx
            ON reply_records (account_slot, sent_at DESC, updated_at DESC);
        CREATE INDEX reply_records_inbound_event_idx
            ON reply_records (inbound_event_id, updated_at DESC);
    """,
    5: """
        ALTER TABLE reply_rules
            ADD COLUMN comment_enabled INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE reply_rules
            ADD COLUMN private_message_enabled INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE reply_rules
            ADD COLUMN auto_categories_json TEXT NOT NULL
                DEFAULT '["question", "praise", "discussion", "consultation"]';
        ALTER TABLE reply_rules
            ADD COLUMN account_profile TEXT NOT NULL DEFAULT '';
        ALTER TABLE reply_rules
            ADD COLUMN knowledge TEXT NOT NULL DEFAULT '';

        CREATE TABLE reply_cycles (
            cycle_id TEXT PRIMARY KEY,
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'xiaohongshu',
            source TEXT NOT NULL DEFAULT 'manual',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            result_state TEXT NOT NULL DEFAULT 'RUNNING',
            summary_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX reply_cycles_account_time_idx
            ON reply_cycles (account_slot, started_at DESC);

        CREATE TABLE reply_decisions (
            decision_id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL,
            inbound_event_id TEXT NOT NULL UNIQUE,
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'xiaohongshu',
            channel TEXT NOT NULL,
            decision TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '',
            intent TEXT NOT NULL DEFAULT '',
            confidence REAL NOT NULL DEFAULT 0,
            reply_text TEXT NOT NULL DEFAULT '',
            facts_used_json TEXT NOT NULL DEFAULT '[]',
            missing_information_json TEXT NOT NULL DEFAULT '[]',
            reply_task_id TEXT NOT NULL DEFAULT '',
            result_state TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (cycle_id) REFERENCES reply_cycles(cycle_id)
        );

        CREATE INDEX reply_decisions_account_decision_idx
            ON reply_decisions (account_slot, decision, updated_at DESC);
    """,
    6: """
        CREATE TABLE private_message_conversations (
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL DEFAULT 'xiaohongshu',
            user_id TEXT NOT NULL,
            owner_state TEXT NOT NULL DEFAULT 'CODEX',
            handled_through_message_count INTEGER NOT NULL DEFAULT 0,
            handoff_event_id TEXT NOT NULL DEFAULT '',
            resume_after_message_count INTEGER NOT NULL DEFAULT 0,
            last_observed_message_count INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (account_slot, platform, user_id)
        );

        CREATE INDEX private_message_conversations_owner_idx
            ON private_message_conversations (account_slot, owner_state, updated_at DESC);
    """,
    7: """
        ALTER TABLE private_message_conversations
            ADD COLUMN resume_source TEXT NOT NULL DEFAULT '';
        ALTER TABLE private_message_conversations
            ADD COLUMN matched_reply_task_id TEXT NOT NULL DEFAULT '';
    """,
    8: """
        ALTER TABLE reply_rules
            ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 1;

        CREATE TABLE reply_rule_versions (
            rule_id TEXT NOT NULL,
            rule_version INTEGER NOT NULL,
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL,
            enabled INTEGER NOT NULL,
            content_scope TEXT NOT NULL,
            reply_style TEXT NOT NULL,
            active_time_range TEXT NOT NULL,
            hourly_limit INTEGER NOT NULL,
            daily_limit INTEGER NOT NULL,
            comment_enabled INTEGER NOT NULL,
            private_message_enabled INTEGER NOT NULL,
            auto_categories_json TEXT NOT NULL,
            manual_categories_json TEXT NOT NULL,
            account_profile TEXT NOT NULL,
            knowledge TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (rule_id, rule_version),
            FOREIGN KEY (rule_id) REFERENCES reply_rules(rule_id)
        );

        INSERT INTO reply_rule_versions (
            rule_id, rule_version, account_slot, platform, enabled,
            content_scope, reply_style, active_time_range, hourly_limit,
            daily_limit, comment_enabled, private_message_enabled,
            auto_categories_json, manual_categories_json, account_profile,
            knowledge, created_at, updated_at
        )
        SELECT
            rule_id, 1, account_slot, platform, enabled,
            content_scope, reply_style, active_time_range, hourly_limit,
            daily_limit, comment_enabled, private_message_enabled,
            auto_categories_json, manual_categories_json, account_profile,
            knowledge, created_at, updated_at
        FROM reply_rules;

        ALTER TABLE reply_decisions
            ADD COLUMN rule_id TEXT NOT NULL DEFAULT '';
        ALTER TABLE reply_decisions
            ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE reply_records
            ADD COLUMN rule_id TEXT NOT NULL DEFAULT '';
        ALTER TABLE reply_records
            ADD COLUMN rule_version INTEGER NOT NULL DEFAULT 0;

        CREATE INDEX reply_rule_versions_account_idx
            ON reply_rule_versions (account_slot, rule_id, rule_version DESC);
        CREATE INDEX reply_decisions_rule_idx
            ON reply_decisions (rule_id, rule_version, updated_at DESC);
        CREATE INDEX reply_records_rule_idx
            ON reply_records (rule_id, rule_version, updated_at DESC);
    """,
    9: """
        CREATE TABLE operation_reports (
            report_id TEXT PRIMARY KEY,
            root_report_id TEXT NOT NULL,
            previous_report_id TEXT NOT NULL DEFAULT '',
            revision_number INTEGER NOT NULL,
            report_type TEXT NOT NULL,
            account_slot TEXT NOT NULL,
            platform TEXT NOT NULL,
            entity_type TEXT NOT NULL DEFAULT '',
            entity_id TEXT NOT NULL DEFAULT '',
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            summary_json TEXT NOT NULL,
            details_json TEXT NOT NULL,
            anomalies_json TEXT NOT NULL,
            revision_reason TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE (root_report_id, revision_number)
        );

        CREATE INDEX operation_reports_account_time_idx
            ON operation_reports (account_slot, report_type, period_end DESC);
        CREATE INDEX operation_reports_revision_idx
            ON operation_reports (root_report_id, revision_number DESC);
    """,
}


class OperationsDatabase:
    """Small connection manager with ordered schema migrations."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        path: str | Path | None = None,
    ) -> None:
        if path is not None:
            self.path = Path(path).expanduser().resolve()
        else:
            base = Path(root).expanduser().resolve() if root else product_root()
            self.path = base / "operations.db"
        self._lock = threading.RLock()
        self.initialize()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as connection:
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if current > SCHEMA_VERSION:
                raise RuntimeError("运营数据库版本高于当前程序支持范围")
            for version in range(current + 1, SCHEMA_VERSION + 1):
                connection.executescript(_MIGRATIONS[version])
                connection.execute(f"PRAGMA user_version = {version}")
            connection.commit()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connect() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fetch_one(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> dict | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(statement, parameters).fetchone()
            return dict(row) if row is not None else None

    def fetch_all(
        self,
        statement: str,
        parameters: Sequence[object] = (),
    ) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(statement, parameters).fetchall()
            return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection
