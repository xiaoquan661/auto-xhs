"""Read loaded notification pages to an explicit history boundary."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

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


def read_notification_history(
    page: Any, reader: Callable[[], list], *, max_items: int = 500
) -> tuple[list[dict], bool, str]:
    rows: dict[str, dict] = {}
    unchanged = 0
    for _ in range(30):
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
        exhausted = page.evaluate(_HISTORY_STATE) is True
        if len(rows) >= max_items:
            return list(rows.values())[:max_items], False, "达到本轮采集数量上限"
        if exhausted:
            return list(rows.values()), True, "已到通知历史末尾"
        unchanged = unchanged + 1 if len(rows) == before else 0
        if unchanged >= 2:
            return list(rows.values()), False, "页面未继续加载，尚未到历史末尾"
        page.evaluate(_SCROLL_HISTORY)
    return list(rows.values()), False, "达到本轮翻页上限"
