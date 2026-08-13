"""V1 quotas, deduplication, and simple account circuit breaking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from product_store import ProductStore
from service_errors import ServiceError


class QuotaService:
    def __init__(self, store: ProductStore) -> None:
        self.store = store

    def check_l1(self, *, account: str, capability: str, target_key: str) -> None:
        now = datetime.now(UTC)
        limits = self.store.get_setting(
            "l1_limits",
            {"hourly": 20, "daily": 100, "dedup_minutes": 10, "failure_threshold": 3},
        )
        events = [
            item
            for item in self.store.list("events")
            if item.get("account_slot") == account and item.get("risk_level") == "L1"
        ]
        failures = [
            item
            for item in events
            if item.get("state") in {"FAILED", "RESULT_UNKNOWN"}
            and self._time(item) >= now - timedelta(hours=1)
        ]
        if len(failures) >= int(limits["failure_threshold"]):
            raise ServiceError("RISK_BLOCKED", "该账号连续失败，已暂停 L1 操作", 409)
        completed = [item for item in events if item.get("state") == "SUCCESS"]
        if sum(self._time(item) >= now - timedelta(hours=1) for item in completed) >= int(
            limits["hourly"]
        ):
            raise ServiceError("RATE_LIMITED", "已达到该账号每小时 L1 配额", 409)
        if sum(self._time(item) >= now - timedelta(days=1) for item in completed) >= int(
            limits["daily"]
        ):
            raise ServiceError("RATE_LIMITED", "已达到该账号每日 L1 配额", 409)
        duplicate_since = now - timedelta(minutes=int(limits["dedup_minutes"]))
        if any(
            item.get("capability") == capability
            and item.get("target_key") == target_key
            and item.get("state") == "SUCCESS"
            and self._time(item) >= duplicate_since
            for item in events
        ):
            raise ServiceError("DUPLICATE_ACTION", "短时间内已对同一目标执行过相同操作", 409)

    @staticmethod
    def _time(item: dict) -> datetime:
        value = item.get("finished_at") or item.get("started_at") or item.get("created_at")
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
