"""Generate immutable daily, weekly, and note-review reports from metric snapshots."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from itertools import pairwise

from metric_service import ENTITY_TYPES, METRIC_FIELDS
from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError

REPORT_TYPES = {"daily", "weekly", "note_review"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ReportService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def generate(
        self,
        *,
        report_type: str,
        account_slot: str,
        period_start: str,
        period_end: str,
        platform: str = "xiaohongshu",
        entity_type: str = "",
        entity_id: str = "",
    ) -> dict:
        definition = self._definition(
            report_type=report_type,
            account_slot=account_slot,
            platform=platform,
            period_start=period_start,
            period_end=period_end,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        report_id = uuid.uuid4().hex
        return self._create_report(
            **definition,
            report_id=report_id,
            root_report_id=report_id,
            previous_report_id="",
            revision_number=1,
            revision_reason="",
        )

    def revise(self, report_id: str, *, revision_reason: str) -> dict:
        current = self.get(report_id)
        latest = self.database.fetch_one(
            """
            SELECT MAX(revision_number) AS revision_number
            FROM operation_reports
            WHERE root_report_id = ?
            """,
            (current["root_report_id"],),
        )
        return self._create_report(
            report_id=uuid.uuid4().hex,
            root_report_id=current["root_report_id"],
            previous_report_id=current["report_id"],
            revision_number=int((latest or {}).get("revision_number") or 0) + 1,
            revision_reason=revision_reason.strip(),
            report_type=current["report_type"],
            account_slot=current["account_slot"],
            platform=current["platform"],
            period_start=current["period_start"],
            period_end=current["period_end"],
            entity_type=current["entity_type"],
            entity_id=current["entity_id"],
        )

    def get(self, report_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM operation_reports WHERE report_id = ?",
            (report_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "运营报告不存在", 404)
        return self._decode(row)

    def list(
        self,
        *,
        account_slot: str | None = None,
        report_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot)
        if report_type:
            if report_type not in REPORT_TYPES:
                raise ServiceError("INVALID_REPORT_TYPE", "未知运营报告类型")
            clauses.append("report_type = ?")
            values.append(report_type)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM operation_reports
            {where}
            ORDER BY created_at DESC
            LIMIT ?
            """,
            values,
        )
        return [self._decode(row) for row in rows]

    def _create_report(
        self,
        *,
        report_id: str,
        root_report_id: str,
        previous_report_id: str,
        revision_number: int,
        revision_reason: str,
        report_type: str,
        account_slot: str,
        platform: str,
        period_start: str,
        period_end: str,
        entity_type: str,
        entity_id: str,
    ) -> dict:
        definition = self._definition(
            report_type=report_type,
            account_slot=account_slot,
            platform=platform,
            period_start=period_start,
            period_end=period_end,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        snapshots = self._snapshots(**definition)
        if not snapshots:
            raise ServiceError("REPORT_DATA_MISSING", "当前报告范围内没有指标快照", 409)
        summary, details, anomalies = self._build_content(snapshots)
        created_at = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO operation_reports (
                    report_id, root_report_id, previous_report_id, revision_number,
                    report_type, account_slot, platform, entity_type, entity_id,
                    period_start, period_end, summary_json, details_json,
                    anomalies_json, revision_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    root_report_id,
                    previous_report_id,
                    revision_number,
                    definition["report_type"],
                    definition["account_slot"],
                    definition["platform"],
                    definition["entity_type"],
                    definition["entity_id"],
                    definition["period_start"],
                    definition["period_end"],
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    json.dumps(details, ensure_ascii=False, sort_keys=True),
                    json.dumps(anomalies, ensure_ascii=False, sort_keys=True),
                    revision_reason,
                    created_at,
                ),
            )
        return self.get(report_id)

    def _snapshots(self, **definition: str) -> list[dict]:
        clauses = [
            "account_slot = ?",
            "platform = ?",
            "captured_at >= ?",
            "captured_at <= ?",
        ]
        values: list[object] = [
            definition["account_slot"],
            definition["platform"],
            definition["period_start"],
            definition["period_end"],
        ]
        if definition["entity_type"]:
            clauses.append("entity_type = ?")
            values.append(definition["entity_type"])
        if definition["entity_id"]:
            clauses.append("entity_id = ?")
            values.append(definition["entity_id"])
        return self.database.fetch_all(
            f"""
            SELECT * FROM metric_snapshots
            WHERE {" AND ".join(clauses)}
            ORDER BY entity_type, entity_id, captured_at, snapshot_id
            """,
            values,
        )

    @staticmethod
    def _build_content(snapshots: list[dict]) -> tuple[dict, list[dict], list[dict]]:
        grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for snapshot in snapshots:
            grouped[(snapshot["entity_type"], snapshot["entity_id"])].append(snapshot)

        details: list[dict] = []
        anomalies: list[dict] = []
        for (entity_type, entity_id), entity_snapshots in grouped.items():
            increments: dict[str, int | None] = {}
            valid_pair_counts: dict[str, int] = {}
            for snapshot in entity_snapshots:
                for metric in METRIC_FIELDS:
                    if snapshot.get(metric) is None:
                        anomalies.append(
                            {
                                "type": "missing_metric",
                                "entity_type": entity_type,
                                "entity_id": entity_id,
                                "metric": metric,
                                "snapshot_id": snapshot["snapshot_id"],
                                "captured_at": snapshot["captured_at"],
                            }
                        )
            for metric in METRIC_FIELDS:
                valid_deltas: list[int] = []
                for previous, current in pairwise(entity_snapshots):
                    previous_value = previous.get(metric)
                    current_value = current.get(metric)
                    if previous_value is None or current_value is None:
                        continue
                    delta = int(current_value) - int(previous_value)
                    if delta < 0:
                        anomalies.append(
                            {
                                "type": "counter_decreased",
                                "entity_type": entity_type,
                                "entity_id": entity_id,
                                "metric": metric,
                                "from_snapshot_id": previous["snapshot_id"],
                                "to_snapshot_id": current["snapshot_id"],
                                "delta": delta,
                            }
                        )
                        continue
                    valid_deltas.append(delta)
                increments[metric] = sum(valid_deltas) if valid_deltas else None
                valid_pair_counts[metric] = len(valid_deltas)
            if len(entity_snapshots) < 2:
                anomalies.append(
                    {
                        "type": "insufficient_history",
                        "entity_type": entity_type,
                        "entity_id": entity_id,
                        "snapshot_id": entity_snapshots[0]["snapshot_id"],
                    }
                )
            details.append(
                {
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "snapshot_count": len(entity_snapshots),
                    "from": entity_snapshots[0]["captured_at"],
                    "to": entity_snapshots[-1]["captured_at"],
                    "increments": increments,
                    "valid_pair_counts": valid_pair_counts,
                    "snapshot_ids": [item["snapshot_id"] for item in entity_snapshots],
                }
            )

        totals: dict[str, int | None] = {}
        for metric in METRIC_FIELDS:
            values = [
                detail["increments"][metric]
                for detail in details
                if detail["increments"][metric] is not None
            ]
            totals[metric] = sum(values) if values else None
        summary = {
            "entity_count": len(details),
            "snapshot_count": len(snapshots),
            "increments": totals,
            "anomaly_count": len(anomalies),
            "missing_metric_count": sum(item["type"] == "missing_metric" for item in anomalies),
        }
        return summary, details, anomalies

    @staticmethod
    def _definition(
        *,
        report_type: str,
        account_slot: str,
        platform: str,
        period_start: str,
        period_end: str,
        entity_type: str,
        entity_id: str,
    ) -> dict[str, str]:
        if report_type not in REPORT_TYPES:
            raise ServiceError("INVALID_REPORT_TYPE", "报告类型只能是日报、周报或单篇复盘")
        account_slot = account_slot.strip()
        if not account_slot:
            raise ServiceError("INVALID_REPORT", "运营报告必须指定账号槽位")
        if entity_type and entity_type not in ENTITY_TYPES:
            raise ServiceError("INVALID_REPORT", "报告对象只能是 account 或 note")
        if report_type == "note_review":
            entity_type = "note"
            if not entity_id.strip():
                raise ServiceError("INVALID_REPORT", "单篇复盘必须指定笔记 ID")
        start = ReportService._normalize_time(period_start)
        end = ReportService._normalize_time(period_end)
        if start > end:
            raise ServiceError("INVALID_REPORT", "报告开始时间不能晚于结束时间")
        return {
            "report_type": report_type,
            "account_slot": account_slot,
            "platform": normalize_platform(platform),
            "period_start": start,
            "period_end": end,
            "entity_type": entity_type,
            "entity_id": entity_id.strip(),
        }

    @staticmethod
    def _normalize_time(value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ServiceError("INVALID_REPORT", "报告时间必须使用 ISO 8601 格式") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["summary"] = json.loads(decoded.pop("summary_json") or "{}")
        decoded["details"] = json.loads(decoded.pop("details_json") or "[]")
        decoded["anomalies"] = json.loads(decoded.pop("anomalies_json") or "[]")
        return decoded
