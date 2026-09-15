"""Temporal meaning of collected events, independent of arrival and reply state."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

ZONE = timezone(timedelta(hours=8))
GROWTH_ACTION = re.compile(
    r"开始关注了?你|关注了你|赞了你的|点赞了你的|收藏了你的|followed you|liked your", re.I
)


def event_time(row: dict) -> dict[str, Any]:
    """Project legacy evidence without rewriting its original timestamp or payload."""
    payload = row.get("payload") or row
    kind = row.get("event_type")
    content = str(payload.get("content") or "")
    source = payload.get("source") or ""
    scope = payload.get("time_scope") or row.get("time_scope") or ""
    display = str(payload.get("display_time") or row.get("display_time") or "")
    observed_at = payload.get("captured_at") or row.get("discovered_at") or ""
    occurred_at = payload.get("event_occurred_at") or row.get("occurred_at") or ""
    reason = ""
    if kind in {"new_follower", "received_like"} and source == "notification_dom":
        actions = GROWTH_ACTION.findall(content)
        if len(actions) != 1 or "沪ICP备" in content:
            reason = "通知容器包含多条事件或整页内容"
        elif kind == "received_like" and re.search(r"收藏了你的", content):
            reason = "收藏事件不计入点赞"
    if reason:
        return {"occurred_at": None, "observed_at": observed_at,
                "time_basis": "invalid_observation", "countable": False, "reason": reason}
    if (kind == "private_message" and source in {"chat_inbox", "chat_inbox_dom"}
            and not payload.get("platform_message_id")):
        return {"occurred_at": None, "observed_at": observed_at or occurred_at,
                "time_basis": "conversation_observation", "countable": False,
                "reason": "会话轮次包含多条消息，缺少逐条平台时间及消息标识"}
    if scope in {"first_observed", "unknown", "relative_approximate"}:
        occurred_at = ""
    # 'N days ago' is rounded elapsed time and may cross two calendar dates.
    if scope == "platform_display_date" and re.fullmatch(r"\d+\s*(分钟|小时|天)前", display):
        occurred_at = ""
    if source == "notification_dom" and not scope:
        occurred_at = ""
    basis = "platform_date" if scope == "platform_display_date" else "platform_timestamp"
    if not occurred_at:
        basis = "unknown"
    return {"occurred_at": occurred_at or None, "observed_at": observed_at,
            "time_basis": basis, "countable": True,
            "reason": "缺少可归属到自然日的平台发生时间" if not occurred_at else ""}


def display_date(text: str, captured_at: str) -> str:
    """Resolve a displayed day, including relative intervals within one calendar day."""
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(ZONE)
    value = text.strip()
    day = None
    if value.startswith("今天") or value == "刚刚":
        day = captured.date()
    elif value.startswith("昨天"):
        day = captured.date() - timedelta(days=1)
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: .*|T.*)?", value):
        day = datetime.strptime(value[:10], "%Y-%m-%d").date()
    elif re.fullmatch(r"\d{2}-\d{2}(?: .*)?", value):
        day = datetime.strptime(f"{captured.year}-{value[:5]}", "%Y-%m-%d").date()
        if day > captured.date():
            day = day.replace(year=day.year - 1)
    else:
        relative = re.fullmatch(r"(\d+)\s*(分钟|小时|天)前", value)
        if relative:
            size = {"分钟": 60, "小时": 3600, "天": 86400}[relative[2]]
            right = captured - timedelta(seconds=int(relative[1]) * size)
            left = right - timedelta(seconds=size)
            if left.date() == right.date():
                day = right.date()
    return f"{day.isoformat()}T00:00:00+08:00" if day else ""


def event_day(row: dict) -> str:
    value = event_time(row)["occurred_at"]
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return ""
    return parsed.replace(tzinfo=parsed.tzinfo or ZONE).astimezone(ZONE).date().isoformat()
