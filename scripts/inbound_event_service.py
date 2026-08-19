"""Persist, deduplicate, and route passive platform events."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from service_errors import ServiceError

EVENT_STATES = {"NEW", "TASK_CREATED", "IGNORED", "HANDLED"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class InboundEventService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def record(
        self,
        *,
        account_slot: str,
        event_type: str,
        platform_event_id: str,
        occurred_at: str,
        object_type: str = "",
        object_id: str = "",
        actor_user_id: str = "",
        payload: dict | None = None,
        classification: str = "",
    ) -> dict:
        account_slot = account_slot.strip()
        event_type = event_type.strip()
        platform_event_id = platform_event_id.strip()
        if not account_slot or not event_type or not platform_event_id:
            raise ServiceError(
                "INVALID_EVENT",
                "平台事件必须包含账号槽位、事件类型和平台事件 ID",
            )
        event_id = uuid.uuid4().hex
        discovered_at = utc_now()
        encoded_payload = json.dumps(payload or {}, ensure_ascii=False, sort_keys=True)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO inbound_events (
                    event_id, account_slot, event_type, platform_event_id,
                    object_type, object_id, actor_user_id, occurred_at,
                    discovered_at, payload_json, classification, handling_state
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'NEW')
                """,
                (
                    event_id,
                    account_slot,
                    event_type,
                    platform_event_id,
                    object_type.strip(),
                    object_id.strip(),
                    actor_user_id.strip(),
                    occurred_at,
                    discovered_at,
                    encoded_payload,
                    classification.strip(),
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT * FROM inbound_events
                WHERE account_slot = ? AND event_type = ? AND platform_event_id = ?
                """,
                (account_slot, event_type, platform_event_id),
            ).fetchone()
        return {"created": created, "event": self._decode(dict(row))}

    def get(self, event_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM inbound_events WHERE event_id = ?",
            (event_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "平台事件不存在", 404)
        return self._decode(row)

    def list(
        self,
        *,
        account_slot: str | None = None,
        handling_state: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot)
        if handling_state:
            clauses.append("handling_state = ?")
            values.append(handling_state)
        if event_type:
            clauses.append("event_type = ?")
            values.append(event_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM inbound_events
            {where}
            ORDER BY occurred_at DESC, discovered_at DESC
            LIMIT ?
            """,
            values,
        )
        return [self._decode(row) for row in rows]

    def attach_task(self, event_id: str, task_id: str) -> dict:
        return self._update_state(event_id, "TASK_CREATED", task_id=task_id)

    def mark_handled(self, event_id: str) -> dict:
        return self._update_state(event_id, "HANDLED")

    def ignore(self, event_id: str) -> dict:
        return self._update_state(event_id, "IGNORED")

    def _update_state(
        self,
        event_id: str,
        state: str,
        *,
        task_id: str | None = None,
    ) -> dict:
        if state not in EVENT_STATES:
            raise ServiceError("INVALID_EVENT_STATE", f"未知平台事件状态: {state}")
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE inbound_events
                SET handling_state = ?, created_task_id = COALESCE(?, created_task_id)
                WHERE event_id = ?
                """,
                (state, task_id, event_id),
            )
            if cursor.rowcount != 1:
                raise ServiceError("NOT_FOUND", "平台事件不存在", 404)
            row = connection.execute(
                "SELECT * FROM inbound_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
        return self._decode(dict(row))

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["payload"] = json.loads(decoded.pop("payload_json") or "{}")
        return decoded
