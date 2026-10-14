"""Read loaded notification pages to an explicit history boundary."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from itertools import pairwise
from typing import Any
from urllib.parse import parse_qs, urlsplit

_HISTORY_STATE = r"""(() => {
  const visible = el => el.getClientRects().length &&
    getComputedStyle(el).visibility !== 'hidden';
  const markers = [...document.querySelectorAll(
    '.end-container, .no-more, .end-text, .empty-text, .empty-description')];
  const end = /没有更多|到底了|暂无消息|暂无通知|还没有收到|no more|no notifications|THE END/i;
  return markers.some(el => visible(el) && end.test(el.innerText || ''));
})()"""
_SCROLL_HISTORY = r"""(() => {
  const anchor = document.querySelector('a[href*="/user/profile/"], .notification-list');
  let node = anchor?.parentElement;
  while (node && node !== document.body) {
    if (node.scrollHeight > node.clientHeight &&
        /auto|scroll/.test(getComputedStyle(node).overflowY)) {
      node.scrollTop = node.scrollHeight; return;
    }
    node = node.parentElement;
  }
  window.scrollTo(0, document.documentElement.scrollHeight);
})()"""
_LOAD_MORE = r"""(() => {
  const button = [...document.querySelectorAll('.load-more-button, .load-more button')]
    .find(el => el.getClientRects().length &&
      /查看更多历史消息|加载更多|load more/i.test(el.innerText || ''));
  if (!button) return false;
  button.click();
  return true;
})()"""

_SELECT_NOTIFICATION_TAB_JS = r"""(async () => {
  const labels = LABELS;
  const normalize = value => String(value || '').replace(/\s+/g, '');
  const findTab = () => [...document.querySelectorAll('[role="tab"], .tab-item, .tab')]
    .find(el => labels.some(label => normalize(label) === normalize(
      (el.querySelector('.badge-container > span') || el).textContent)));
  const selected = el => el.classList.contains('active') ||
    el.getAttribute('aria-selected') === 'true';
  const pause = () => new Promise(resolve => setTimeout(resolve, 250));
  let tab;
  for (let attempt = 0; attempt < 40 && !tab; attempt++) {
    tab = findTab();
    if (!tab) await pause();
  }
  if (!tab) return false;
  if (!selected(tab)) tab.click();
  for (let attempt = 0; attempt < 40; attempt++) {
    tab = findTab();
    if (tab && selected(tab)) return true;
    await pause();
  }
  return false;
})()"""


def select_notification_tab(page: Any, labels: tuple[str, ...]) -> bool:
    """Select a notification label independently of its unread badge."""
    script = _SELECT_NOTIFICATION_TAB_JS.replace("LABELS", json.dumps(labels, ensure_ascii=False))
    return page.evaluate(script) is True


def read_notification_history(
    page: Any,
    reader: Callable[[], list],
    *,
    max_items: int = 500,
    api_exhausted: Callable[[], bool | None] | None = None,
    page_progress: Callable[[], int] | None = None,
) -> tuple[list[dict], bool, str]:
    rows: dict[str, dict] = {}
    unchanged = 0
    progressed_at = time.monotonic()
    previous_page = 0
    for attempt in range(30):
        page.wait_dom_stable()
        values = reader()
        before = len(rows)
        for row in values if isinstance(values, list) else []:
            if isinstance(row, dict):
                key = str(
                    row.get("notification_id")
                    or row.get("comment_id")
                    or json.dumps(row, ensure_ascii=False, sort_keys=True)
                )
                rows[key] = row
        exhausted = api_exhausted() if api_exhausted else None
        if exhausted is None:
            exhausted = page.evaluate(_HISTORY_STATE) is True
        if len(rows) >= max_items:
            return list(rows.values())[:max_items], False, "达到本轮采集数量上限"
        if exhausted:
            return list(rows.values()), True, "已到通知历史末尾"
        current_page = page_progress() if page_progress else 0
        unchanged = unchanged + 1 if len(rows) == before and current_page == previous_page else 0
        previous_page = current_page
        if unchanged:
            if time.monotonic() - progressed_at >= 10:
                return list(rows.values()), False, "页面未继续加载，尚未到历史末尾"
            if attempt:
                time.sleep(0.25)
                continue
        else:
            progressed_at = time.monotonic()
        if page.evaluate(_LOAD_MORE) is not True:
            page.evaluate(_SCROLL_HISTORY)
    return list(rows.values()), False, "达到本轮翻页上限"


def notification_api_complete(pages: list[dict]) -> bool:
    """Use the observed first page, cursor chain and final platform marker."""
    if not pages:
        return False

    def cursor(page: dict) -> str:
        return parse_qs(urlsplit(str(page.get("url") or "")).query).get("cursor", [""])[0]

    data = [(page.get("payload") or {}).get("data") or {} for page in pages]
    if (
        any(not isinstance(item.get("message_list"), list) for item in data)
        or cursor(pages[0])
        or data[-1].get("has_more") is not False
    ):
        return False
    for previous, current in pairwise(pages):
        previous_data = (previous.get("payload") or {}).get("data") or {}
        expected = str(previous_data.get("strCursor") or previous_data.get("cursor") or "")
        if previous_data.get("has_more") is not True or cursor(current) != expected:
            return False
    return True
