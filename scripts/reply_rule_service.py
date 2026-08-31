"""Account-level rules for contextual comment and private-message replies."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError

DEFAULT_AUTO_CATEGORIES = [
    "ordinary",
    "question",
    "praise",
    "discussion",
    "consultation",
]
DEFAULT_MANUAL_CATEGORIES = [
    "complaint",
    "dispute",
    "cooperation",
    "quote",
    "inventory",
    "refund",
    "contact_details",
    "offline_meeting",
    "commitment",
    "insufficient_context",
    "sensitive",
]


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
        active_time_range: str = "",
        hourly_limit: int = 0,
        daily_limit: int = 0,
        comment_enabled: bool = True,
        private_message_enabled: bool = True,
        auto_categories: list[str] | None = None,
        manual_categories: list[str] | None = None,
        account_profile: str = "",
        knowledge: str = "",
    ) -> dict:
        rule_id = uuid.uuid4().hex
        platform = normalize_platform(platform)
        hourly_limit = int(hourly_limit)
        daily_limit = int(daily_limit)
        if hourly_limit < 0 or daily_limit < 0:
            raise ServiceError("INVALID_REPLY_RULE", "回复数量上限不能小于 0")
        self._within_active_time(active_time_range, datetime.now().astimezone())
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_rules (
                    rule_id, account_slot, platform, enabled, content_scope, reply_style,
                    active_time_range, hourly_limit, daily_limit,
                    comment_enabled, private_message_enabled, auto_categories_json,
                    manual_categories_json, account_profile, knowledge,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    account_slot,
                    platform,
                    content_scope,
                    reply_style,
                    active_time_range,
                    hourly_limit,
                    daily_limit,
                    int(bool(comment_enabled)),
                    int(bool(private_message_enabled)),
                    json.dumps(
                        (
                            auto_categories
                            if auto_categories is not None
                            else DEFAULT_AUTO_CATEGORIES
                        ),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        (
                            manual_categories
                            if manual_categories is not None
                            else DEFAULT_MANUAL_CATEGORIES
                        ),
                        ensure_ascii=False,
                    ),
                    account_profile.strip(),
                    knowledge.strip(),
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
            "comment_enabled",
            "private_message_enabled",
            "auto_categories",
            "manual_categories",
            "account_profile",
            "knowledge",
        }
        assignments: list[str] = []
        values: list[object] = []
        for name, value in changes.items():
            if name not in allowed:
                raise ServiceError("INVALID_REQUEST", f"不支持修改回复规则字段: {name}")
            column = {
                "auto_categories": "auto_categories_json",
                "manual_categories": "manual_categories_json",
            }.get(name, name)
            assignments.append(f"{column} = ?")
            if name in {"auto_categories", "manual_categories"}:
                value = json.dumps(value, ensure_ascii=False)
            elif name in {"hourly_limit", "daily_limit"}:
                value = int(value)
                if value < 0:
                    raise ServiceError("INVALID_REPLY_RULE", "回复数量上限不能小于 0")
            elif name in {"comment_enabled", "private_message_enabled"}:
                value = int(bool(value))
            elif name == "active_time_range":
                self._within_active_time(str(value), datetime.now().astimezone())
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
        classification: str,
        channel: str = "comment",
        quality_flags: list[str] | None = None,
        facts_used: list[str] | None = None,
        missing_information: list[str] | None = None,
        now: datetime | None = None,
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
        current = now or datetime.now().astimezone()
        if not self._within_active_time(rule["active_time_range"], current):
            return self._decision(False, "pending", "outside_active_time", rule)
        sent_last_hour, sent_today = self._sent_counts(rule, current)
        if rule["hourly_limit"] and sent_last_hour >= rule["hourly_limit"]:
            return self._decision(False, "pending", "hourly_limit", rule)
        if rule["daily_limit"] and sent_today >= rule["daily_limit"]:
            return self._decision(False, "pending", "daily_limit", rule)
        flags = set(quality_flags or [])
        if "duplicate_recent_reply" in flags:
            return self._decision(False, "skip", "duplicate_reply", rule)
        if flags:
            return self._decision(False, "pending", "quality_check", rule)
        if missing_information:
            return self._decision(False, "pending", "missing_information", rule)
        if classification in {"question", "consultation"} and not facts_used:
            return self._decision(False, "pending", "facts_missing", rule)
        if classification and classification in rule["manual_categories"]:
            return self._decision(False, "pending", "manual_category", rule)
        if classification not in rule["auto_categories"]:
            return self._decision(False, "pending", "category_not_allowed", rule)
        return self._decision(True, "reply", "rule_authorized", rule)

    def _sent_counts(self, rule: dict, now: datetime) -> tuple[int, int]:
        current_utc = now.astimezone(UTC)
        hour_start = current_utc - timedelta(hours=1)
        day_start_local = current_utc.astimezone(now.tzinfo).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        day_start = day_start_local.astimezone(UTC)
        row = (
            self.database.fetch_one(
                """
            SELECT
                SUM(CASE WHEN sent_at >= ? THEN 1 ELSE 0 END) AS hour_count,
                SUM(CASE WHEN sent_at >= ? THEN 1 ELSE 0 END) AS day_count
            FROM reply_records
            WHERE account_slot = ? AND platform = ? AND result_state = 'SUCCESS'
            """,
                (
                    hour_start.isoformat(),
                    day_start.isoformat(),
                    rule["account_slot"],
                    rule["platform"],
                ),
            )
            or {}
        )
        return int(row.get("hour_count") or 0), int(row.get("day_count") or 0)

    @staticmethod
    def _within_active_time(value: str, now: datetime) -> bool:
        text = value.strip()
        if not text:
            return True
        try:
            start_text, end_text = text.split("-", 1)
            start = datetime.strptime(start_text.strip(), "%H:%M").time()
            end = datetime.strptime(end_text.strip(), "%H:%M").time()
        except ValueError as exc:
            raise ServiceError(
                "INVALID_REPLY_RULE",
                "回复时段必须使用 HH:MM-HH:MM 格式",
            ) from exc
        current = now.timetz().replace(tzinfo=None)
        if start <= end:
            return start <= current <= end
        return current >= start or current <= end

    @staticmethod
    def _decision(authorized: bool, decision: str, reason: str, rule: dict) -> dict:
        return {
            "authorized": authorized,
            "decision": decision,
            "reason": reason,
            "rule": rule,
        }

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["enabled"] = bool(decoded["enabled"])
        decoded["comment_enabled"] = bool(decoded.get("comment_enabled", 1))
        decoded["private_message_enabled"] = bool(decoded.get("private_message_enabled", 1))
        decoded["auto_categories"] = json.loads(decoded.pop("auto_categories_json", "[]") or "[]")
        decoded["manual_categories"] = json.loads(decoded.pop("manual_categories_json") or "[]")
        return decoded
