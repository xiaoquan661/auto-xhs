"""Collection intervals shared by workers, outbox and daily summaries."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from metric_temporality import ZONE


def collection_window(captured_at: str) -> tuple[str, str]:
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(ZONE)
    start = (captured - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), captured.isoformat()


def batch_summary(
    rows: list[dict], metric: str, day: str, *, now: datetime | None = None
) -> dict[str, Any]:
    start = datetime.fromisoformat(day).replace(tzinfo=ZONE)
    end = start + timedelta(days=1)
    now = (now or datetime.now(ZONE)).astimezone(ZONE)
    is_today = start.date() == now.date()
    batches = []
    intervals = []
    indexed_intervals = []
    event_index = {}
    for row in rows:
        if row.get("event_type") != "collection_observation":
            continue
        payload = row.get("payload") or {}
        if payload.get("metric") != metric:
            continue
        left, right = payload.get("period_start"), payload.get("period_end")
        if not left or not right:
            continue
        try:
            left = datetime.fromisoformat(left.replace("Z", "+00:00"))
            right = datetime.fromisoformat(right.replace("Z", "+00:00"))
            left = left.replace(tzinfo=left.tzinfo or ZONE)
            right = right.replace(tzinfo=right.tzinfo or ZONE)
        except ValueError:
            continue
        if right <= start or left >= end:
            continue
        batches.append(
            {
                **payload,
                "collected_at": row.get("occurred_at"),
                "batch_id": row.get("platform_event_id"),
            }
        )
        unit = "message" if metric == "received_private_messages" else "event"
        if (
            payload.get("collection_state") == "SUCCESS"
            and payload.get("coverage") == "complete"
            and payload.get("unit") in {unit, "event"}
            and payload.get("undated_count") == 0
        ):
            interval = (max(left, start), min(right, end))
            intervals.append(interval)
            if isinstance(payload.get("event_index"), list):
                indexed_intervals.append(interval)
                event_index.update({r["id"]: r["date"] for r in payload["event_index"]})
    # Today's counter covers the sampled part of the day; the daily ledger retains
    # the full calendar interval and is finalized after midnight.
    if is_today:
        end = max((min(datetime.fromisoformat(b["period_end"].replace("Z", "+00:00")), now)
                   for b in batches), default=start)
    cursor = start
    gaps = []
    for left, right in sorted(intervals):
        if left > cursor:
            gaps.append({"start": cursor.isoformat(), "end": left.isoformat()})
        cursor = max(cursor, right)
    if cursor < end:
        gaps.append({"start": cursor.isoformat(), "end": end.isoformat()})
    reasons = list(
        dict.fromkeys(
            str(b.get("reason") or "采集范围未完整覆盖")
            for b in batches
            if b.get("coverage") != "complete" and gaps
            and any(
                datetime.fromisoformat(b["period_start"].replace("Z", "+00:00"))
                < datetime.fromisoformat(g["end"])
                and datetime.fromisoformat(b["period_end"].replace("Z", "+00:00"))
                > datetime.fromisoformat(g["start"])
                for g in gaps
            )
        )
    )
    indexed_cursor = start
    indexed_gap = False
    for left, right in sorted(indexed_intervals):
        indexed_gap = indexed_gap or left > indexed_cursor
        indexed_cursor = max(indexed_cursor, right)
    indexed_complete = bool(indexed_intervals) and not indexed_gap and indexed_cursor >= end
    return {
        "confirmed_count": sum(d == day for d in event_index.values())
        if indexed_complete
        else None,
        "complete": bool(intervals) and not gaps,
        "is_today": is_today,
        "as_of": end.isoformat() if batches else None,
        "day_finalized": not is_today and end <= now and bool(intervals) and not gaps,
        "batches": batches,
        "missing_intervals": gaps,
        "reasons": reasons,
        "batch_count": len(batches),
        "last_collected_at": max((b.get("collected_at") or "" for b in batches), default=""),
    }


def observations_for_result(task: dict, result: dict, captured_at: str) -> list[dict]:
    """Each real collector provides its own channel-specific coverage evidence."""
    batches = result.get("collection_batches") or []
    if task.get("capability") == "collect-operations-metrics":
        batches = (result.get("growth_notifications") or {}).get("collection_batches") or batches
    return [
        {
            "event_type": "collection_observation",
            "platform_event_id": f"{task['task_id']}:{index}:{batch['metric']}",
            "occurred_at": captured_at,
            "account_slot": task["account_slot"],
            "payload": {
                **batch,
                "task_id": task["task_id"],
                "collection_state": "SUCCESS"
                if batch.get("coverage") == "complete"
                else "PARTIAL_SUCCESS",
            },
        }
        for index, batch in enumerate(batches)
    ]


def covered_dates(row: dict) -> list[str]:
    """Include days with a collection batch even when no events occurred."""
    if row.get("event_type") != "collection_observation":
        return []
    payload = row.get("payload") or {}
    try:
        start = datetime.fromisoformat(payload["period_start"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(payload["period_end"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return []
    start = start.replace(tzinfo=start.tzinfo or ZONE).astimezone(ZONE)
    end = end.replace(tzinfo=end.tzinfo or ZONE).astimezone(ZONE)
    if end <= start:
        return []
    current = start.date()
    last = (end - timedelta(microseconds=1)).date()
    days = []
    while current <= last:
        days.append(current.isoformat())
        current += timedelta(days=1)
    return days
