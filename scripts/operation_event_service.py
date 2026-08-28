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
                    operation_event_id, task_id, account_slot, platform, capability,
                    action, target_type, target_id, started_at, finished_at,
                    result_state, result_summary, error_code, recommended_action,
                    platform_result_json, readback_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_event_id,
                    task["task_id"],
                    task["account_slot"],
                    str(task.get("platform") or "xiaohongshu"),
                    task["capability"],
                    self._action(task),
                    str(task.get("target_type") or ""),
                    str(task.get("target_id") or ""),
                    task.get("started_at"),
                    task.get("finished_at"),
                    task["state"],
                    str(task.get("result_summary") or ""),
                    str(task.get("error_code") or ""),
                    str(task.get("recommended_action") or ""),
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

    def get_by_task(self, task_id: str) -> dict:
        row = self.database.fetch_one(
            """
            SELECT * FROM operation_events
            WHERE task_id = ?
            ORDER BY finished_at DESC
            LIMIT 1
            """,
            (task_id,),
        )
        return self._decode(row) if row else {}

    def reconcile_result_unknown(self, task_id: str, *, readback: dict) -> dict:
        current = self.get_by_task(task_id)
        if not current:
            return {}
        merged_readback = {**dict(current.get("readback") or {}), **readback}
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE operation_events
                SET result_state = 'SUCCESS',
                    result_summary = '延迟回读确认私信已发送',
                    error_code = '', recommended_action = '', readback_json = ?
                WHERE operation_event_id = ? AND result_state = 'RESULT_UNKNOWN'
                """,
                (
                    json.dumps(merged_readback, ensure_ascii=False, sort_keys=True),
                    current["operation_event_id"],
                ),
            )
        return self.get(current["operation_event_id"])

    def list(
        self,
        *,
        account_slot: str | None = None,
        capability: str | None = None,
        action: str | None = None,
        result_state: str | None = None,
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
        if action:
            clauses.append("action = ?")
            values.append(action)
        if result_state:
            clauses.append("result_state = ?")
            values.append(result_state)
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
    def _action(task: dict) -> str:
        operation = str(task.get("operation") or "")
        capability = str(task.get("capability") or "")
        if (
            operation in {"reviewed_reply", "automatic_reply"}
            and capability == "send-private-messages"
        ):
            return "private-message-reply"
        if operation == "automatic_reply" and capability == "reply-comment":
            return "comment-reply"
        return operation or capability

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["platform_result"] = json.loads(
            decoded.pop("platform_result_json") or "{}"
        )
        decoded["readback"] = json.loads(decoded.pop("readback_json") or "{}")
        return decoded
