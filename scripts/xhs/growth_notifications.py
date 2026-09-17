"""Read visible follower/like notifications through the account's browser."""

from __future__ import annotations

import itertools
import re
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

from collection_coverage import collection_window
from metric_temporality import GROWTH_ACTION, display_date, event_day

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
      const actors = new Set([...card.querySelectorAll('a[href*="/user/profile/"]')]
        .map(a => new URL(a.href).pathname));
      const actionPattern = new RegExp(
        '开始关注了?你|关注了你|赞了你的|点赞了你的|收藏了你的|followed you|liked your', 'gi');
      const actions = text.match(actionPattern) || [];
      if (actors.size !== 1 || actions.length !== 1 || text.includes('沪ICP备')) break;
      if (kind === 'received_like' && /收藏了你的/.test(text)) break;
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
        content = str(row.get("content") or "")
        if content and (len(GROWTH_ACTION.findall(content)) != 1 or "沪ICP备" in content):
            continue
        if kind == "received_like" and "收藏了你的" in content:
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
            display = str(row.get("display_time") or "")
            at = _display_date(display, captured_at)
            if at:
                scope = (
                    "platform_interval_date" if display.endswith("前") else "platform_display_date"
                )
            elif re.fullmatch(r"\d+\s*(分钟|小时|天)前", display):
                scope = "relative_approximate"
        # Relative labels and URL tracking parameters change between collections.
        target = urlsplit(str(row.get("target_url") or "")).path
        identity_text = content.replace(str(row.get("display_time") or ""), "").strip()
        identifier = str(row.get("notification_id") or "") or ":".join(
            [str(kind), user, target, identity_text or at or "undated"]
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
                "metric_semantics": "event",
                "identity_basis": "platform_id" if row.get("notification_id") else "content_key",
                "event_occurred_at": at or None,
                "source": "notification_dom",
            }
        )
    return events


def _display_date(text: str, captured_at: str) -> str:
    return display_date(text, captured_at)


def collect_growth_notifications(page: Any, *, max_items: int = 100) -> dict:
    page.navigate(NOTIFICATION_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    page.evaluate(_CAPTURE_GROWTH_JS)
    rows: list[dict] = []
    tabs = []
    batches = []
    captured = datetime.now(UTC).isoformat()
    start, end = collection_window(captured)
    for metric, labels in (
        ("received_likes", ("赞和收藏", "赞与收藏", "Likes and saves")),
        ("new_followers", ("新增关注", "关注", "New followers")),
    ):
        import json

        select_script = """(() => {
          const labels = LABELS;
          const tab = [...document.querySelectorAll('[role="tab"], .tab-item, .tab')]
            .find(el => labels.includes((el.innerText || '').trim()));
          if (!tab) return false;
          tab.click();
          return tab.classList.contains("active") || tab.getAttribute("aria-selected") === "true";
        })()""".replace("LABELS", json.dumps(labels, ensure_ascii=False))
        selected = False
        for _ in range(40):
            if page.evaluate(select_script) is True:
                selected = True
                break
            time.sleep(0.25)
        tabs.append(bool(selected))
        channel_rows = []
        exhausted = False
        stop = "通知标签未就绪"
        if selected:
            from .notification_history import read_notification_history

            # The tab is visible before its asynchronous notification response arrives.
            for _ in range(40):
                observed = page.evaluate("window.__AUTO_XHS_GROWTH__ || {}")
                if parse_growth_api(observed, metric, captured)[0] is not None:
                    break
                time.sleep(0.25)
            channel_rows, exhausted, stop = read_notification_history(
                page, lambda: page.evaluate(_READ_GROWTH_JS), max_items=max_items
            )
        observed_api = page.evaluate("window.__AUTO_XHS_GROWTH__ || {}")
        api_rows, api_complete = parse_growth_api(observed_api, metric, captured)
        kind = "received_like" if metric == "received_likes" else "new_follower"
        if api_rows is not None:
            normalized = api_rows
            exhausted = api_complete
            undated = sum(not event_day(row) for row in normalized)
            invalid = 0
            unidentified = 0
            stop = "通知接口分页尚未完整返回"
        else:
            normalized = normalize_growth_notifications(channel_rows, captured)
            normalized = [row for row in normalized if row["event_type"] == kind]
            undated = sum(not event_day(row) for row in normalized)
            invalid = len([r for r in channel_rows if r.get("event_type") == kind]) - len(
                normalized
            )
            unidentified = sum(r.get("identity_basis") != "platform_id" for r in normalized)
        if len(normalized) >= max_items:
            normalized = normalized[:max_items]
            exhausted = False
            stop = "达到本轮采集数量上限"
        complete = exhausted and not undated and not unidentified and invalid == 0
        rows.extend(normalized)
        batches.append(
            {
                "metric": metric,
                "source": "notification_history",
                "period_start": start,
                "period_end": end,
                "unit": "event",
                "coverage": "complete" if complete else "partial",
                "undated_count": undated,
                "invalid_count": invalid,
                "unidentified_count": unidentified,
                "observed_count": len(normalized),
                "source_exhausted": exhausted,
                "event_index": [
                    {"id": r["platform_event_id"], "date": event_day(r)} for r in normalized
                ],
                "reason": ""
                if complete
                else (
                    "部分通知缺少可归属日期"
                    if undated
                    else "部分通知结构无法识别"
                    if invalid
                    else "通知缺少平台事件标识"
                    if unidentified
                    else stop
                ),
                "cursor": normalized[-1]["platform_event_id"] if normalized else "",
            }
        )
    return {
        "events": rows,
        "captured_at": captured,
        "partial": any(b["coverage"] != "complete" for b in batches),
        "coverage": "complete" if all(b["coverage"] == "complete" for b in batches) else "partial",
        "tabs_observed": tabs,
        "collection_batches": batches,
    }


_CAPTURE_GROWTH_JS = r"""(() => {
  if (window.__AUTO_XHS_GROWTH__) return;
  const captured = window.__AUTO_XHS_GROWTH__ = {};
  const record = (url, body) => {
    const path = new URL(url, location.href).pathname;
    if (!/\/you\/(likes|connections)$/.test(path)) return;
    try {
      const page = {url, payload: typeof body === 'string' ? JSON.parse(body) : body};
      const pages = (captured[path]?.pages || []).filter(p => p.url !== url);
      captured[path] = {...page, pages: [...pages,page]};
    }
    catch (_) {}
  };
  const originalFetch = window.fetch;
  window.fetch = async function(...args) {
    const response = await originalFetch.apply(this,args);
    if (response.status === 200 && /\/you\/(likes|connections)/.test(response.url))
      response.clone().text().then(body => record(response.url,body));
    return response;
  };
  const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function(method,url,...args) {
    this.__growthUrl = url; return open.call(this,method,url,...args);
  };
  XMLHttpRequest.prototype.send = function(...args) {
    if (/\/you\/(likes|connections)/.test(this.__growthUrl || ''))
      this.addEventListener('load',function() {
        if (this.status === 200) record(this.__growthUrl,
          this.responseType === 'json' ? this.response : this.responseText);
      },{once:true});
    return send.apply(this,args);
  };
})()"""


def parse_growth_api(captured: dict, metric: str, captured_at: str) -> tuple[list | None, bool]:
    """Normalize the platform's observed notification responses."""
    path = "/api/sns/web/v1/you/" + ("likes" if metric == "received_likes" else "connections")
    response = captured.get(path) if isinstance(captured, dict) else None
    if not response:
        return None, False
    pages = response.get("pages") or [response]
    rows = {}
    valid = True
    for page in pages:
        data = (page.get("payload") or {}).get("data") or {}
        items = data.get("message_list")
        if not isinstance(items, list):
            valid = False
            continue
        for item in items:
            kind = str(item.get("type") or "")
            if metric == "received_likes" and kind.startswith("faved/"):
                continue
            accepted = (
                kind.startswith("liked/") if metric == "received_likes" else kind == "follow/you"
            )
            if not accepted or not item.get("id") or not item.get("time"):
                valid = False
                continue
            user = item.get("user_info") or item.get("user") or {}
            note = item.get("item_info") or {}
            identifier = str(item["id"])
            occurred = datetime.fromtimestamp(int(item["time"]), UTC).isoformat()
            rows[identifier] = {
                "event_type": "received_like" if metric == "received_likes" else "new_follower",
                "platform_event_id": identifier,
                "notification_id": identifier,
                "actor_user_id": str(user.get("userid") or ""),
                "user_id": str(user.get("userid") or ""),
                "nickname": user.get("nickname", ""),
                "target_url": note.get("link", ""),
                "content": item.get("title", ""),
                "occurred_at": occurred,
                "event_occurred_at": occurred,
                "captured_at": captured_at,
                "time_scope": "platform",
                "source": "notification_api",
                "identity_basis": "platform_id",
                "metric_semantics": "event",
            }
    first_cursor = parse_qs(urlsplit(pages[0]["url"]).query).get("cursor", [""])[0]
    last = (pages[-1].get("payload") or {}).get("data") or {}
    for previous, current in itertools.pairwise(pages):
        previous_data = (previous.get("payload") or {}).get("data") or {}
        expected = str(previous_data.get("strCursor") or previous_data.get("cursor") or "")
        cursor = parse_qs(urlsplit(current["url"]).query).get("cursor", [""])[0]
        valid = valid and previous_data.get("has_more") is True and cursor == expected
    complete = valid and not first_cursor and last.get("has_more") is False
    return list(rows.values()), complete
