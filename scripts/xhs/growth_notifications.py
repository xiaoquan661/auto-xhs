"""Read visible follower/like notifications through the account's browser."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from .urls import NOTIFICATION_URL

_READ_GROWTH_JS = r"""
(() => {
  const rows = [], seen = new Set();
  for (const link of document.querySelectorAll('a[href*="/user/profile/"]')) {
    let card = link.parentElement;
    for (let depth = 0; card && depth < 5; depth++, card = card.parentElement) {
      const text = card.innerText || '';
      const kind = /开始关注了你|关注了你|followed you/i.test(text) ? 'new_follower'
        : /赞了你的|点赞了你的|liked your/i.test(text) ? 'received_like' : '';
      if (!kind || text.length > 1500) continue;
      if (seen.has(card)) break;
      seen.add(card);
      const time = card.querySelector('time, [data-time], [class*="time"]');
      const note = card.querySelector('a[href*="/explore/"]');
      rows.push({event_type: kind, user_id: new URL(link.href).pathname.split('/').pop(),
        nickname: (link.innerText || '').trim(), profile_url: link.href,
        target_url: note?.href || '', content: text.trim(),
        notification_id: card.getAttribute('data-notification-id')
          || card.getAttribute('data-id') || '',
        occurred_at: time?.getAttribute('datetime') || time?.getAttribute('data-time') || '',
        display_time: (time?.innerText || '').trim()});
      break;
    }
  }
  return rows;
})()
"""


def normalize_growth_notifications(rows: list[dict], captured_at: str) -> list[dict]:
    events: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        kind = row.get("event_type")
        user = str(row.get("user_id") or "")
        if kind not in {"new_follower", "received_like"} or not user:
            continue
        at = str(row.get("occurred_at") or "")
        if at.isdigit():
            epoch = float(at)
            at = datetime.fromtimestamp(
                epoch / 1000 if epoch > 10_000_000_000 else epoch, UTC
            ).isoformat()
        try:
            datetime.fromisoformat(at.replace("Z", "+00:00"))
        except ValueError:
            at = ""
        scope = "platform" if at else "first_observed"
        if not at:
            at = _display_date(str(row.get("display_time") or ""), captured_at)
            if at:
                scope = "platform_display_date"
        identifier = str(row.get("notification_id") or "") or ":".join(
            [str(kind), user, str(row.get("target_url") or ""), at or "undated"]
        )
        if identifier in seen:
            continue
        seen.add(identifier)
        events.append(
            {
                **row,
                "platform_event_id": identifier,
                "actor_user_id": user,
                "occurred_at": at or captured_at,
                "captured_at": captured_at,
                "time_scope": scope,
                "source": "notification_dom",
            }
        )
    return events


def _display_date(text: str, captured_at: str) -> str:
    """Preserve the day visible in a notification without inventing an exact time."""
    captured = datetime.fromisoformat(captured_at.replace("Z", "+00:00")).astimezone(
        timezone(timedelta(hours=8))
    )
    value = text.strip()
    day = None
    if value.startswith("今天") or value == "刚刚":
        day = captured.date()
    elif value.startswith("昨天"):
        day = captured.date() - timedelta(days=1)
    elif match := re.fullmatch(r"(\d+)\s*(分钟|小时|天)前", value):
        amount, unit = int(match[1]), match[2]
        offset = timedelta(**{{"分钟": "minutes", "小时": "hours", "天": "days"}[unit]: amount})
        day = (captured - offset).date()
    elif re.fullmatch(r"\d{4}-\d{2}-\d{2}(?: .*)?", value):
        day = datetime.strptime(value[:10], "%Y-%m-%d").date()
    if day is None:
        return ""
    return f"{day.isoformat()}T00:00:00+08:00"


def collect_growth_notifications(page: Any, *, max_items: int = 100) -> dict:
    page.navigate(NOTIFICATION_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    rows: list[dict] = []
    tabs = []
    for labels in (
        ("赞和收藏", "赞与收藏", "Likes and saves"),
        ("新增关注", "关注", "New followers"),
    ):
        import json

        selected = page.evaluate(
            """(() => {
          const labels = LABELS;
          const tab = [...document.querySelectorAll('[role="tab"], .tab-item, .tab')]
            .find(el => labels.includes((el.innerText || '').trim()));
          if (!tab) return false;
          tab.click(); return true;
        })()""".replace("LABELS", json.dumps(labels, ensure_ascii=False))
        )
        tabs.append(bool(selected))
        if selected:
            page.wait_dom_stable()
        observed = page.evaluate(_READ_GROWTH_JS)
        if isinstance(observed, list):
            rows.extend(observed)
    captured = datetime.now(UTC).isoformat()
    events = normalize_growth_notifications(rows, captured)[:max_items]
    return {
        "events": events,
        "captured_at": captured,
        "partial": True,
        "coverage": "visible_notifications",
        "tabs_observed": tabs,
    }
