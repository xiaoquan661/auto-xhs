"""Persist read-only collector progress independently for each account."""

from __future__ import annotations

from datetime import UTC, datetime

from operations_db import OperationsDatabase


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class CollectorService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def get(self, account_slot: str, collector_type: str) -> dict | None:
        return self.database.fetch_one(
            """
            SELECT * FROM collector_cursors
            WHERE account_slot = ? AND collector_type = ?
            """,
            (account_slot, collector_type),
        )

    def list(self, *, account_slot: str | None = None) -> list[dict]:
        if account_slot:
            return self.database.fetch_all(
                """
                SELECT * FROM collector_cursors
                WHERE account_slot = ?
                ORDER BY collector_type
                """,
                (account_slot,),
            )
        return self.database.fetch_all(
            """
            SELECT * FROM collector_cursors
            ORDER BY account_slot, collector_type
            """
        )

    def record_success(
        self,
        *,
        account_slot: str,
        collector_type: str,
        cursor_value: str = "",
        last_seen_time: str | None = None,
    ) -> dict:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO collector_cursors (
                    account_slot, collector_type, cursor_value, last_seen_time,
                    last_success_time, last_attempt_time, last_error
                ) VALUES (?, ?, ?, ?, ?, ?, '')
                ON CONFLICT(account_slot, collector_type) DO UPDATE SET
                    cursor_value = excluded.cursor_value,
                    last_seen_time = excluded.last_seen_time,
                    last_success_time = excluded.last_success_time,
                    last_attempt_time = excluded.last_attempt_time,
                    last_error = ''
                """,
                (
                    account_slot,
                    collector_type,
                    cursor_value,
                    last_seen_time,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM collector_cursors
                WHERE account_slot = ? AND collector_type = ?
                """,
                (account_slot, collector_type),
            ).fetchone()
        return dict(row)

    def record_failure(
        self,
        *,
        account_slot: str,
        collector_type: str,
        error: str,
    ) -> dict:
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO collector_cursors (
                    account_slot, collector_type, last_attempt_time, last_error
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(account_slot, collector_type) DO UPDATE SET
                    last_attempt_time = excluded.last_attempt_time,
                    last_error = excluded.last_error
                """,
                (account_slot, collector_type, now, error),
            )
            row = connection.execute(
                """
                SELECT * FROM collector_cursors
                WHERE account_slot = ? AND collector_type = ?
                """,
                (account_slot, collector_type),
            ).fetchone()
        return dict(row)
