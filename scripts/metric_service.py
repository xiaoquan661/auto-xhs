"""Time-series snapshots for account and note operations metrics."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from service_errors import ServiceError

METRIC_FIELDS = (
    "likes",
    "favorites",
    "comments",
    "shares",
    "followers",
    "following",
    "views",
    "impressions",
    "notes",
)
ENTITY_TYPES = {"account", "note"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class MetricService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def record_snapshot(
        self,
        *,
        account_slot: str,
        entity_type: str,
        entity_id: str,
        source: str,
        metrics: dict,
        captured_at: str | None = None,
        extra: dict | None = None,
    ) -> dict:
        if entity_type not in ENTITY_TYPES:
            raise ServiceError("INVALID_METRIC_ENTITY", "指标对象只能是 account 或 note")
        captured_at = captured_at or utc_now()
        values = {name: self._optional_int(metrics.get(name)) for name in METRIC_FIELDS}
        snapshot_id = uuid.uuid4().hex
        with self.database.transaction() as connection:
            cursor = connection.execute(
                f"""
                INSERT OR IGNORE INTO metric_snapshots (
                    snapshot_id, account_slot, entity_type, entity_id,
                    captured_at, source, {', '.join(METRIC_FIELDS)}, extra_json
                ) VALUES ({', '.join('?' for _ in range(6 + len(METRIC_FIELDS) + 1))})
                """,
                (
                    snapshot_id,
                    account_slot,
                    entity_type,
                    entity_id,
                    captured_at,
                    source,
                    *(values[name] for name in METRIC_FIELDS),
                    json.dumps(extra or {}, ensure_ascii=False, sort_keys=True),
                ),
            )
            created = cursor.rowcount == 1
            row = connection.execute(
                """
                SELECT * FROM metric_snapshots
                WHERE account_slot = ? AND entity_type = ? AND entity_id = ?
                  AND captured_at = ? AND source = ?
                """,
                (account_slot, entity_type, entity_id, captured_at, source),
            ).fetchone()
        return {"created": created, "snapshot": self._decode(dict(row))}

    def history(
        self,
        *,
        account_slot: str,
        entity_type: str,
        entity_id: str,
        limit: int = 100,
    ) -> list[dict]:
        rows = self.database.fetch_all(
            """
            SELECT * FROM metric_snapshots
            WHERE account_slot = ? AND entity_type = ? AND entity_id = ?
            ORDER BY captured_at DESC
            LIMIT ?
            """,
            (account_slot, entity_type, entity_id, max(1, int(limit))),
        )
        return [self._decode(row) for row in rows]

    def latest_delta(
        self,
        *,
        account_slot: str,
        entity_type: str,
        entity_id: str,
    ) -> dict | None:
        snapshots = self.history(
            account_slot=account_slot,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=2,
        )
        if len(snapshots) < 2:
            return None
        current, previous = snapshots[0], snapshots[1]
        delta = {
            name: (
                current[name] - previous[name]
                if current[name] is not None and previous[name] is not None
                else None
            )
            for name in METRIC_FIELDS
        }
        return {
            "account_slot": account_slot,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "from": previous["captured_at"],
            "to": current["captured_at"],
            "delta": delta,
        }

    @staticmethod
    def _optional_int(value) -> int | None:
        if value is None or value == "":
            return None
        return int(value)

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["extra"] = json.loads(decoded.pop("extra_json") or "{}")
        return decoded
