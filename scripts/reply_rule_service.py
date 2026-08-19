"""Account-level reply rules; automatic execution remains opt-in."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from service_errors import ServiceError

DEFAULT_MANUAL_CATEGORIES = ["sensitive", "dispute", "complaint"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ReplyRuleService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        account_slot: str,
        content_scope: str = "all_own_notes",
        reply_style: str = "natural",
        active_time_range: str = "",
        hourly_limit: int = 0,
        daily_limit: int = 0,
        manual_categories: list[str] | None = None,
    ) -> dict:
        rule_id = uuid.uuid4().hex
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_rules (
                    rule_id, account_slot, enabled, content_scope, reply_style,
                    active_time_range, hourly_limit, daily_limit,
                    manual_categories_json, created_at, updated_at
                ) VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    account_slot,
                    content_scope,
                    reply_style,
                    active_time_range,
                    int(hourly_limit),
                    int(daily_limit),
                    json.dumps(
                        manual_categories or DEFAULT_MANUAL_CATEGORIES,
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
        return self.get(rule_id)

    def get(self, rule_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM reply_rules WHERE rule_id = ?",
            (rule_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "回复规则不存在", 404)
        return self._decode(row)

    def list(self, *, account_slot: str | None = None) -> list[dict]:
        if account_slot:
            rows = self.database.fetch_all(
                """
                SELECT * FROM reply_rules
                WHERE account_slot = ?
                ORDER BY updated_at DESC
                """,
                (account_slot,),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT * FROM reply_rules ORDER BY account_slot, updated_at DESC"
            )
        return [self._decode(row) for row in rows]

    def update(self, rule_id: str, **changes) -> dict:
        allowed = {
            "content_scope",
            "reply_style",
            "active_time_range",
            "hourly_limit",
            "daily_limit",
            "manual_categories",
        }
        assignments: list[str] = []
        values: list[object] = []
        for name, value in changes.items():
            if name not in allowed:
                raise ServiceError("INVALID_REQUEST", f"不支持修改回复规则字段: {name}")
            column = "manual_categories_json" if name == "manual_categories" else name
            assignments.append(f"{column} = ?")
            if name == "manual_categories":
                value = json.dumps(value, ensure_ascii=False)
            elif name in {"hourly_limit", "daily_limit"}:
                value = int(value)
            values.append(value)
        if not assignments:
            return self.get(rule_id)
        assignments.append("updated_at = ?")
        values.append(utc_now())
        values.append(rule_id)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"UPDATE reply_rules SET {', '.join(assignments)} WHERE rule_id = ?",
                values,
            )
            if cursor.rowcount != 1:
                raise ServiceError("NOT_FOUND", "回复规则不存在", 404)
        return self.get(rule_id)

    def set_enabled(self, rule_id: str, enabled: bool) -> dict:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE reply_rules SET enabled = ?, updated_at = ? WHERE rule_id = ?",
                (int(bool(enabled)), utc_now(), rule_id),
            )
            if cursor.rowcount != 1:
                raise ServiceError("NOT_FOUND", "回复规则不存在", 404)
        return self.get(rule_id)

    def decision(self, rule_id: str, *, classification: str) -> dict:
        rule = self.get(rule_id)
        if not rule["enabled"]:
            return {"authorized": False, "reason": "rule_disabled", "rule": rule}
        if classification and classification in rule["manual_categories"]:
            return {"authorized": False, "reason": "manual_category", "rule": rule}
        return {"authorized": True, "reason": "rule_enabled", "rule": rule}

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["enabled"] = bool(decoded["enabled"])
        decoded["manual_categories"] = json.loads(
            decoded.pop("manual_categories_json") or "[]"
        )
        return decoded
