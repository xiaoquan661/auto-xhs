"""SQLite storage for inbound platform events and long-running operations data."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from product_store import product_root

SCHEMA_VERSION = 2

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
