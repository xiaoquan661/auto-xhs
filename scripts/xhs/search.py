"""搜索 Feeds，对应 Go xiaohongshu/search.go。"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Callable

from .cdp import Page
from .errors import NoFeedsError, XHSError
from .selectors import FILTER_BUTTON, FILTER_PANEL
from .types import Feed, FilterOption
from .urls import make_search_url

logger = logging.getLogger(__name__)

# 筛选选项映射表：{筛选组索引: [文本, ...]}
_FILTER_OPTIONS: dict[int, list[str]] = {
    1: ["综合", "最新", "最多点赞", "最多评论", "最多收藏"],
    2: ["不限", "视频", "图文"],
    3: ["不限", "一天内", "一周内", "半年内"],
    4: ["不限", "已看过", "未看过", "已关注"],
    5: ["不限", "同城", "附近"],
}

# 从 __INITIAL_STATE__ 提取搜索结果的 JS。不同站点版本会使用不同字段，
# 但条目结构仍可在 Python 侧统一规范化为 Feed。
_EXTRACT_SEARCH_JS = """
(() => {
    const state = window.__INITIAL_STATE__ || {};
    const search = state.search || {};
    const searchResult = state.searchResult || {};
    const unwrap = (value) => {
        if (value?.value !== undefined) return value.value;
        if (value?._value !== undefined) return value._value;
        return value;
    };
    const pools = [
        unwrap(search.feeds),
        unwrap(search.noteResults),
        unwrap(search.items),
        unwrap(searchResult.items),
    ];
    const data = pools.find((items) => Array.isArray(items) && items.length > 0);
    return data ? JSON.stringify(data) : "";
})()
"""

_SEARCH_DIAGNOSTICS_JS = """
(() => {
    const state = window.__INITIAL_STATE__ || {};
    const search = state.search || {};
    const searchResult = state.searchResult || {};
    const size = (value) => {
        const data = value?.value !== undefined ? value.value : value?._value;
        return Array.isArray(data) ? data.length : (Array.isArray(value) ? value.length : null);
    };
    return {
        url: location.href,
        title: document.title,
        stateKeys: Object.keys(state).sort(),
        searchKeys: Object.keys(search).sort(),
        searchResultKeys: Object.keys(searchResult).sort(),
        counts: {
            searchFeeds: size(search.feeds),
            searchNoteResults: size(search.noteResults),
            searchItems: size(search.items),
            searchResultItems: size(searchResult.items),
        },
        noteLinkCount: document.querySelectorAll(
            'a[href*="/explore/"], a[href*="/discovery/item/"]'
        ).length,
    };
})()
"""


def _find_internal_option(group_index: int, text: str) -> tuple[int, str]:
    """查找内部筛选选项。

    Returns:
        (filters_index, text)

    Raises:
        ValueError: 未找到匹配的选项。
    """
    options = _FILTER_OPTIONS.get(group_index)
    if not options:
        raise ValueError(f"筛选组 {group_index} 不存在")

    if text in options:
        return group_index, text

    raise ValueError(f"在筛选组 {group_index} 中未找到 '{text}'，有效值: {options}")


def _convert_filters(filter_opt: FilterOption) -> list[tuple[int, str]]:
    """将 FilterOption 转换为内部 (filters_index, text) 列表。"""
    result: list[tuple[int, str]] = []

    if filter_opt.sort_by:
        result.append(_find_internal_option(1, filter_opt.sort_by))
    if filter_opt.note_type:
        result.append(_find_internal_option(2, filter_opt.note_type))
    if filter_opt.publish_time:
        result.append(_find_internal_option(3, filter_opt.publish_time))
    if filter_opt.search_scope:
        result.append(_find_internal_option(4, filter_opt.search_scope))
    if filter_opt.location:
        result.append(_find_internal_option(5, filter_opt.location))

    return result


def search_feeds(
    page: Page,
    keyword: str,
    filter_option: FilterOption | None = None,
) -> list[Feed]:
    """搜索 Feeds。

    Args:
        page: CDP 页面对象。
        keyword: 搜索关键词。
        filter_option: 可选筛选条件。

    Raises:
        NoFeedsError: 没有捕获到搜索结果。
        ValueError: 筛选选项无效。
    """
    search_url = make_search_url(keyword)
    try:
        page.navigate(search_url)
    except XHSError:
        logger.warning("搜索导航失败，页面状态：%s", page.evaluate(_SEARCH_DIAGNOSTICS_JS))
        raise
    page.wait_for_load()
    page.wait_dom_stable()

    # 等待 __INITIAL_STATE__.search.feeds 有数据
    _wait_for_search_feeds(page)

    # 应用筛选条件（若有）
    if filter_option:
        internal_filters = _convert_filters(filter_option)
        if internal_filters:
            _apply_filters(page, internal_filters)

    # 提取搜索结果
    result = page.evaluate(_EXTRACT_SEARCH_JS)
    if not result:
        raise NoFeedsError()

    feeds = _decode_search_feeds(result)
    if not feeds:
        raise NoFeedsError()

    return feeds


def collect_search_feeds(
    page: Page,
    keyword: str,
    filter_option: FilterOption | None = None,
    *,
    target_count: int = 20,
    duration_seconds: int = 120,
    accept: Callable[[Feed], bool] | None = None,
    max_empty_scrolls: int = 5,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[list[Feed], dict]:
    """Search and collect a deduplicated candidate pool while scrolling."""

    if target_count < 1:
        raise ValueError("候选池数量必须大于 0")
    if duration_seconds < 1:
        raise ValueError("搜集时间必须大于 0")

    started = time.monotonic()
    initial = search_feeds(page, keyword, filter_option)
    seen: set[str] = set()
    collected: dict[str, Feed] = {}

    def add_feeds(feeds: list[Feed]) -> int:
        added = 0
        for feed in feeds:
            if not feed.id or feed.id in seen:
                continue
            seen.add(feed.id)
            if accept is not None and not accept(feed):
                continue
            collected[feed.id] = feed
            added += 1
        return added

    add_feeds(initial)
    scroll_count = 0
    empty_scrolls = 0
    deadline = started + duration_seconds

    while len(collected) < target_count and time.monotonic() < deadline:
        viewport = int(page.evaluate("window.innerHeight || 768") or 768)
        page.scroll_by(0, int(viewport * random.uniform(0.72, 0.96)))
        scroll_count += 1
        sleeper(min(random.uniform(0.9, 1.5), max(0.0, deadline - time.monotonic())))

        result = page.evaluate(_EXTRACT_SEARCH_JS)
        feeds = _decode_search_feeds(result) if result else []
        if add_feeds(feeds):
            empty_scrolls = 0
        else:
            empty_scrolls += 1
            if empty_scrolls >= max_empty_scrolls:
                break

    if len(collected) >= target_count:
        stop_reason = "pool_reached"
    elif time.monotonic() >= deadline:
        stop_reason = "time_limit"
    else:
        stop_reason = "no_new_results"
    return list(collected.values())[:target_count], {
        "collected_count": min(len(collected), target_count),
        "total_seen_count": len(seen),
        "scroll_count": scroll_count,
        "collection_elapsed_seconds": round(time.monotonic() - started, 2),
        "collection_stop_reason": stop_reason,
    }


def _wait_for_search_feeds(page: Page, timeout: float | None = None) -> None:
    """等待 __INITIAL_STATE__.search.feeds 有数据。

    Raises:
        NoFeedsError: 超时仍无数据。
    """
    if timeout is None:
        timeout = 30.0 if getattr(page, "platform", "xiaohongshu") == "rednote" else 15.0
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = page.evaluate(_EXTRACT_SEARCH_JS)
        if result:
            try:
                if _decode_search_feeds(result):
                    return
            except json.JSONDecodeError:
                pass
        time.sleep(0.3)
    try:
        logger.warning(
            "搜索结果状态诊断（仅结构，不含内容）: %s",
            page.evaluate(_SEARCH_DIAGNOSTICS_JS),
        )
    except Exception as exc:
        logger.warning("搜索结果状态诊断失败: %s", exc)
    raise NoFeedsError()


def _decode_search_feeds(result: str) -> list[Feed]:
    """Normalize supported search-state variants and discard non-note entries."""
    raw_items = json.loads(result)
    if not isinstance(raw_items, list):
        return []

    feeds: list[Feed] = []
    for index, item in enumerate(raw_items):
        if not isinstance(item, dict):
            continue
        note = item.get("note") if isinstance(item.get("note"), dict) else {}
        note_card = item.get("noteCard")
        if not isinstance(note_card, dict):
            nested_card = note.get("noteCard")
            note_card = nested_card if isinstance(nested_card, dict) else note

        note_id = str(
            item.get("id") or item.get("noteId") or note.get("id") or note.get("noteId") or ""
        ).strip()
        xsec_token = str(item.get("xsecToken") or note.get("xsecToken") or "").strip()
        model_type = str(item.get("modelType") or "note").strip()
        if not note_id or "#" in note_id or not xsec_token or model_type == "hot_query":
            continue

        feeds.append(
            Feed.from_dict(
                {
                    "id": note_id,
                    "xsecToken": xsec_token,
                    "modelType": model_type,
                    "index": item.get("index", index),
                    "noteCard": note_card,
                }
            )
        )
    return feeds


def _apply_filters(page: Page, filters: list[tuple[int, str]]) -> None:
    """应用筛选条件。

    在单次 evaluate 调用内完成：点击筛选按钮 → 等待面板 → 按文本点击各选项
    → 等待搜索结果刷新。
    避免多次 WebSocket 连接导致面板关闭的时序问题。
    """
    filter_js_list = ", ".join(
        f'[{idx}, {json.dumps(text)}]' for idx, text in filters
    )

    # 记录当前 feeds 快照，用于检测结果是否已刷新
    snapshot_js = "JSON.stringify(window.__INITIAL_STATE__?.search?.feeds?.value ?? window.__INITIAL_STATE__?.search?.feeds?._value ?? null)"

    script = f"""
(() => {{
  return new Promise((resolve, reject) => {{
    const btn = document.querySelector({json.dumps(FILTER_BUTTON)});
    if (!btn) {{ reject('筛选按钮不存在'); return; }}

    // 记录点击前的 feeds 快照（用于判断结果已刷新）
    const snapshot = {snapshot_js};
    btn.click();

    const items = [{filter_js_list}];
    let attempts = 0;

    // 等待筛选面板出现
    const panelTimer = setInterval(() => {{
      const wrapper = document.querySelector({json.dumps(FILTER_PANEL)});
      if (!wrapper) {{
        if (++attempts > 50) {{ clearInterval(panelTimer); reject('筛选面板等待超时'); }}
        return;
      }}
      clearInterval(panelTimer);

      // 依次点击各筛选项
      for (const [groupIdx, text] of items) {{
        const group = wrapper.querySelectorAll('div.filters')[groupIdx - 1];
        if (!group) {{ reject('筛选组 ' + groupIdx + ' 不存在'); return; }}
        const tag = Array.from(group.querySelectorAll('div.tags'))
          .find(el => el.textContent.trim() === text);
        if (!tag) {{ reject('选项不存在: ' + text); return; }}
        tag.click();
      }}

      // 等待搜索结果刷新（feeds 快照变化）
      let refreshAttempts = 0;
      const refreshTimer = setInterval(() => {{
        const current = {snapshot_js};
        if (current !== snapshot) {{
          clearInterval(refreshTimer);
          resolve(null);
          return;
        }}
        if (++refreshAttempts > 60) {{
          // 超时也继续（结果可能未变化）
          clearInterval(refreshTimer);
          resolve(null);
        }}
      }}, 100);
    }}, 100);
  }});
}})()
"""
    try:
        page.evaluate(script)
    except Exception as e:
        raise ValueError(f"应用筛选失败: {e}") from e

    # 等待 __INITIAL_STATE__ 中有新数据
    _wait_for_search_feeds(page)
