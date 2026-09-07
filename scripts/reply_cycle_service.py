"""Persist bounded automatic-reply cycles and per-event decisions."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError

DECISIONS = {"reply", "pending", "skip"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ReplyCycleService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def start(
        self,
        *,
        account_slot: str,
        platform: str = "xiaohongshu",
        source: str = "manual",
    ) -> dict:
        cycle_id = uuid.uuid4().hex
        started_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_cycles (
                    cycle_id, account_slot, platform, source, started_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    cycle_id,
                    account_slot.strip(),
                    normalize_platform(platform),
                    source.strip() or "manual",
                    started_at,
                ),
            )
        return self.get(cycle_id)

    def finish(self, cycle_id: str, *, result_state: str, summary: dict) -> dict:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE reply_cycles
                SET finished_at = ?, result_state = ?, summary_json = ?
                WHERE cycle_id = ?
                """,
                (
                    utc_now(),
                    result_state.strip(),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    cycle_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ServiceError("NOT_FOUND", "自动回复周期不存在", 404)
        return self.get(cycle_id)

    def record_decision(
        self,
        *,
        cycle_id: str,
        event: dict,
        decision: str,
        reason: str,
        generation: dict | None = None,
        rule: dict | None = None,
        reply_task_id: str = "",
        result_state: str = "",
    ) -> dict:
        if decision not in DECISIONS:
            raise ServiceError("INVALID_REQUEST", f"未知回复决策: {decision}")
        generation = dict(generation or {})
        rule = dict(rule or {})
        event_type = str(event.get("event_type") or "")
        channel = "private_message" if event_type == "private_message" else "comment"
        now = utc_now()
        decision_id = uuid.uuid4().hex
        values = (
            decision_id,
            cycle_id,
            str(event.get("event_id") or ""),
            str(event.get("account_slot") or ""),
            normalize_platform(event.get("platform")),
            channel,
            decision,
            reason,
            str(generation.get("intent") or ""),
            float(generation.get("confidence") or 0),
            str(generation.get("reply") or ""),
            json.dumps(generation.get("facts_used") or [], ensure_ascii=False),
            json.dumps(
                generation.get("missing_information") or [],
                ensure_ascii=False,
            ),
            reply_task_id,
            result_state,
            str(rule.get("rule_id") or ""),
            int(rule.get("rule_version") or 0),
            now,
            now,
        )
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_decisions (
                    decision_id, cycle_id, inbound_event_id, account_slot, platform,
                    channel, decision, reason, intent, confidence, reply_text,
                    facts_used_json, missing_information_json, reply_task_id,
                    result_state, rule_id, rule_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(inbound_event_id) DO UPDATE SET
                    cycle_id = excluded.cycle_id,
                    decision = excluded.decision,
                    reason = excluded.reason,
                    intent = excluded.intent,
                    confidence = excluded.confidence,
                    reply_text = excluded.reply_text,
                    facts_used_json = excluded.facts_used_json,
                    missing_information_json = excluded.missing_information_json,
                    reply_task_id = excluded.reply_task_id,
                    result_state = excluded.result_state,
                    rule_id = excluded.rule_id,
                    rule_version = excluded.rule_version,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            row = connection.execute(
                "SELECT * FROM reply_decisions WHERE inbound_event_id = ?",
                (str(event.get("event_id") or ""),),
            ).fetchone()
        return self._decode_decision(dict(row))

    def get(self, cycle_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM reply_cycles WHERE cycle_id = ?",
            (cycle_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "自动回复周期不存在", 404)
        return self._decode_cycle(row)

    def list_cycles(
        self,
        *,
        account_slot: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        if account_slot:
            rows = self.database.fetch_all(
                """
                SELECT * FROM reply_cycles
                WHERE account_slot = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (account_slot, max(1, int(limit))),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT * FROM reply_cycles ORDER BY started_at DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        return [self._decode_cycle(row) for row in rows]

    def list_decisions(
        self,
        *,
        account_slot: str | None = None,
        decision: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot)
        if decision:
            clauses.append("decision = ?")
            values.append(decision)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM reply_decisions
            {where}
            ORDER BY updated_at DESC LIMIT ?
            """,
            values,
        )
        return [self._decode_decision(row) for row in rows]

    def reconcile_result_unknown(
        self,
        *,
        inbound_event_id: str,
        reply_task_id: str,
    ) -> dict:
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE reply_decisions
                SET decision = 'reply', reason = 'sent_with_delayed_readback',
                    result_state = 'SUCCESS', updated_at = ?
                WHERE inbound_event_id = ? AND reply_task_id = ?
                  AND result_state = 'RESULT_UNKNOWN'
                """,
                (utc_now(), inbound_event_id, reply_task_id),
            )
            row = connection.execute(
                "SELECT * FROM reply_decisions WHERE inbound_event_id = ?",
                (inbound_event_id,),
            ).fetchone()
        return self._decode_decision(dict(row)) if row else {}

    @staticmethod
    def _decode_cycle(row: dict) -> dict:
        decoded = dict(row)
        decoded["summary"] = json.loads(decoded.pop("summary_json") or "{}")
        return decoded

    @staticmethod
    def _decode_decision(row: dict) -> dict:
        decoded = dict(row)
        decoded["facts_used"] = json.loads(decoded.pop("facts_used_json") or "[]")
        decoded["missing_information"] = json.loads(decoded.pop("missing_information_json") or "[]")
        return decoded
