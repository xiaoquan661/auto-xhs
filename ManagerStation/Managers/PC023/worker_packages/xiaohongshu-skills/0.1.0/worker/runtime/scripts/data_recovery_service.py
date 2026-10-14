"""Aggregate the three business ledgers shown in the local data workspace."""

from __future__ import annotations

from operation_event_service import OperationEventService
from operations_db import OperationsDatabase
from published_post_service import PublishedPostService
from reply_record_service import ReplyRecordService


class DataRecoveryService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database
        self.published_posts = PublishedPostService(database)
        self.operation_events = OperationEventService(database)
        self.reply_records = ReplyRecordService(database)

    def summary(self) -> dict:
        return {
            "published_posts": self._group_summary("published_posts", "result_state"),
            "operation_records": self._group_summary("operation_events", "result_state"),
            "reply_records": self._group_summary("reply_records", "result_state"),
            "reply_channels": self._group_counts("reply_records", "channel"),
            "operation_actions": self._group_counts("operation_events", "action"),
        }

    def _group_summary(self, table: str, state_column: str) -> dict:
        total = self.database.fetch_one(f"SELECT COUNT(*) AS count FROM {table}")
        return {
            "total": int((total or {}).get("count") or 0),
            "states": self._group_counts(table, state_column),
        }

    def _group_counts(self, table: str, column: str) -> dict[str, int]:
        rows = self.database.fetch_all(
            f"""
            SELECT {column} AS name, COUNT(*) AS count
            FROM {table}
            GROUP BY {column}
            ORDER BY {column}
            """
        )
        return {
            str(row["name"] or "UNKNOWN"): int(row["count"])
            for row in rows
        }
