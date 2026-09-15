"""Account-level rules for contextual comment and private-message replies."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ReplyRuleService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def create(
        self,
        *,
        account_slot: str,
        platform: str = "xiaohongshu",
        content_scope: str = "all_own_notes",
        reply_style: str = "natural",
        comment_enabled: bool = True,
        private_message_enabled: bool = True,
        account_profile: str = "",
        knowledge: str = "",
    ) -> dict:
        rule_id = uuid.uuid4().hex
        platform = normalize_platform(platform)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_rules (
                    rule_id, account_slot, platform, enabled, content_scope, reply_style,
                    comment_enabled, private_message_enabled, account_profile, knowledge,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    account_slot,
                    platform,
                    content_scope,
                    reply_style,
                    int(bool(comment_enabled)),
                    int(bool(private_message_enabled)),
                    account_profile.strip(),
                    knowledge.strip(),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reply_rules WHERE rule_id = ?",
                (rule_id,),
            ).fetchone()
            self._insert_version(connection, dict(row))
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

    def get_version(self, rule_id: str, rule_version: int) -> dict:
        row = self.database.fetch_one(
            """
            SELECT * FROM reply_rule_versions
            WHERE rule_id = ? AND rule_version = ?
            """,
            (rule_id, int(rule_version)),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "回复规则版本不存在", 404)
        return self._decode(row)

    def list_versions(self, rule_id: str) -> list[dict]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM reply_rule_versions
            WHERE rule_id = ?
            ORDER BY rule_version DESC
            """,
            (rule_id,),
        )
        return [self._decode(row) for row in rows]

    def update(self, rule_id: str, **changes) -> dict:
        allowed = {
            "content_scope",
            "reply_style",
            "comment_enabled",
            "private_message_enabled",
            "account_profile",
            "knowledge",
        }
        assignments: list[str] = []
        values: list[object] = []
        for name, value in changes.items():
            if name not in allowed:
                raise ServiceError("INVALID_REQUEST", f"不支持修改回复规则字段: {name}")
            assignments.append(f"{name} = ?")
            if name in {"comment_enabled", "private_message_enabled"}:
                value = int(bool(value))
            values.append(value)
        if not assignments:
            return self.get(rule_id)
        assignments.append("rule_version = rule_version + 1")
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
            row = connection.execute(
                "SELECT * FROM reply_rules WHERE rule_id = ?",
                (rule_id,),
            ).fetchone()
            self._insert_version(connection, dict(row))
        return self.get(rule_id)

    def set_enabled(self, rule_id: str, enabled: bool) -> dict:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE reply_rules
                SET enabled = ?, rule_version = rule_version + 1, updated_at = ?
                WHERE rule_id = ?
                """,
                (int(bool(enabled)), utc_now(), rule_id),
            )
            if cursor.rowcount != 1:
                raise ServiceError("NOT_FOUND", "回复规则不存在", 404)
            row = connection.execute(
                "SELECT * FROM reply_rules WHERE rule_id = ?",
                (rule_id,),
            ).fetchone()
            self._insert_version(connection, dict(row))
        return self.get(rule_id)

    def resolve(self, account_slot: str, *, platform: str = "xiaohongshu") -> dict:
        rows = self.database.fetch_all(
            """
            SELECT * FROM reply_rules
            WHERE account_slot = ? AND platform = ?
            ORDER BY enabled DESC, updated_at DESC
            LIMIT 1
            """,
            (account_slot, normalize_platform(platform)),
        )
        if not rows:
            raise ServiceError("REPLY_RULE_MISSING", "该账号尚未配置自动回复规则", 409)
        return self._decode(rows[0])

    def decision(
        self,
        rule_id: str,
        *,
        channel: str = "comment",
    ) -> dict:
        rule = self.get(rule_id)
        if not rule["enabled"]:
            return self._decision(False, "skip", "rule_disabled", rule)
        channel_field = {
            "comment": "comment_enabled",
            "private_message": "private_message_enabled",
        }.get(channel)
        if not channel_field:
            raise ServiceError("INVALID_REQUEST", f"未知回复渠道: {channel}")
        if not rule[channel_field]:
            return self._decision(False, "skip", "channel_disabled", rule)
        return self._decision(True, "reply", "rule_authorized", rule)

    @staticmethod
    def _decision(authorized: bool, decision: str, reason: str, rule: dict) -> dict:
        return {
            "authorized": authorized,
            "decision": decision,
            "reason": reason,
            "rule": rule,
        }

    @staticmethod
    def _insert_version(connection: sqlite3.Connection, row: dict) -> None:
        connection.execute(
            """
            INSERT INTO reply_rule_versions (
                rule_id, rule_version, account_slot, platform, enabled,
                content_scope, reply_style,
                comment_enabled, private_message_enabled,
                account_profile,
                knowledge, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["rule_id"],
                row["rule_version"],
                row["account_slot"],
                row["platform"],
                row["enabled"],
                row["content_scope"],
                row["reply_style"],
                row["comment_enabled"],
                row["private_message_enabled"],
                row["account_profile"],
                row["knowledge"],
                row["created_at"],
                row["updated_at"],
            ),
        )

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["enabled"] = bool(decoded["enabled"])
        decoded["comment_enabled"] = bool(decoded.get("comment_enabled", 1))
        decoded["private_message_enabled"] = bool(decoded.get("private_message_enabled", 1))
        return decoded
