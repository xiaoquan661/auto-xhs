"""Structured execution records stored alongside inbound events and metrics."""

from __future__ import annotations

import json
import uuid

from operations_db import OperationsDatabase


class OperationEventService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def record(self, task: dict, *, result: dict | None = None, readback: dict | None = None) -> dict:
        operation_event_id = uuid.uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO operation_events (
                    operation_event_id, task_id, account_slot, capability,
                    target_type, target_id, started_at, finished_at,
                    result_state, platform_result_json, readback_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_event_id,
                    task["task_id"],
                    task["account_slot"],
                    task["capability"],
                    str(task.get("target_type") or ""),
                    str(task.get("target_id") or ""),
                    task.get("started_at"),
                    task.get("finished_at"),
                    task["state"],
                    json.dumps(result or {}, ensure_ascii=False, sort_keys=True),
                    json.dumps(readback or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
        return self.get(operation_event_id)

    def get(self, operation_event_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM operation_events WHERE operation_event_id = ?",
            (operation_event_id,),
        )
        return self._decode(row) if row else {}

    def list(
        self,
        *,
        account_slot: str | None = None,
        capability: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot)
        if capability:
            clauses.append("capability = ?")
            values.append(capability)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM operation_events
            {where}
            ORDER BY finished_at DESC
            LIMIT ?
            """,
            values,
        )
        return [self._decode(row) for row in rows]

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["platform_result"] = json.loads(
            decoded.pop("platform_result_json") or "{}"
        )
        decoded["readback"] = json.loads(decoded.pop("readback_json") or "{}")
        return decoded
