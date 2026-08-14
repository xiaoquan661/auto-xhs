"""Browse feed cards and like them while preserving the same feed page session."""

from __future__ import annotations

import logging
import random
import time

from .errors import XHSError
from .feeds import extract_current_feeds
from .like_favorite import like_current_feed
from .selectors import LIKE_BUTTON
from .types import Feed
from .urls import HOME_URL

logger = logging.getLogger(__name__)

_CLOSE_SELECTORS = (
    ".close-circle",
    ".note-detail-mask .close",
    ".note-container .close",
    'button[aria-label="关闭"]',
    '[class*="note-detail"] [class*="close"]',
)


def _card_selector(feed_id: str) -> str:
    return f'a[href*="/explore/{feed_id}"]'


def _wait_until(page, predicate, timeout: float = 10.0, interval: float = 0.25) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _detail_is_open(page, feed_id: str) -> bool:
    current_url = str(page.evaluate("window.location.href") or "")
    return feed_id in current_url and page.has_element(LIKE_BUTTON)


def _open_from_feed(page, feed: Feed) -> None:
    selector = _card_selector(feed.id)
    if not page.has_element(selector):
        raise XHSError(f"信息流中未找到笔记卡片: {feed.id}")

    page.scroll_element_into_view(selector)
    time.sleep(random.uniform(0.8, 1.4))
    page.click_element(selector)

    if not _wait_until(page, lambda: _detail_is_open(page, feed.id), timeout=12.0):
        raise XHSError(f"进入笔记详情页超时: {feed.id}")


def _wait_detail_closed(page, feed_id: str) -> bool:
    return _wait_until(page, lambda: not _detail_is_open(page, feed_id), timeout=6.0)


def _close_detail(page, feed_id: str) -> str:
    for selector in _CLOSE_SELECTORS:
        if not page.has_element(selector):
            continue
        page.click_element(selector)
        if _wait_detail_closed(page, feed_id):
            return "close_button"

    page.press_key("Escape")
    if _wait_detail_closed(page, feed_id):
        return "escape"

    page.evaluate("window.history.back()")
    if _wait_detail_closed(page, feed_id):
        return "history_back"

    raise XHSError(f"无法退出笔记详情页: {feed_id}")


def _select_candidates(page, count: int, video_only: bool) -> list[Feed]:
    feeds = extract_current_feeds(page)
    candidates = [
        feed
        for feed in feeds
        if feed.model_type == "note"
        and feed.id
        and feed.xsec_token
        and (not video_only or feed.note_card.type == "video")
        and not feed.note_card.interact_info.liked
        and page.has_element(_card_selector(feed.id))
    ]
    if len(candidates) < count:
        kind = "视频" if video_only else "笔记"
        raise XHSError(f"当前信息流中仅找到 {len(candidates)} 条可点赞{kind}，不足 {count} 条")

    # Randomize the items, then restore feed order so each next item is below the last.
    return sorted(random.sample(candidates, count), key=lambda feed: feed.index)


def _browse_candidates(page, seen: set[str]) -> list[Feed]:
    return [
        feed
        for feed in extract_current_feeds(page)
        if feed.model_type == "note"
        and feed.id
        and feed.id not in seen
        and page.has_element(_card_selector(feed.id))
    ]


def _simulate_reading(page, duration_seconds: float, deadline: float) -> float:
    """Scroll inside an opened note for the allocated time budget."""
    started = time.monotonic()
    budget_end = min(deadline, started + max(0.0, duration_seconds))
    viewport = int(page.evaluate("window.innerHeight || 768") or 768)
    width = int(page.evaluate("window.innerWidth || 1280") or 1280)
    while time.monotonic() < budget_end:
        page.mouse_move(
            random.uniform(width * 0.35, width * 0.72),
            random.uniform(viewport * 0.28, viewport * 0.68),
        )
        page.dispatch_wheel_event(random.uniform(viewport * 0.18, viewport * 0.38))
        time.sleep(min(random.uniform(1.2, 2.4), max(0.0, budget_end - time.monotonic())))
    return round(max(0.0, time.monotonic() - started), 2)


def browse_feed_cycle(page, *, duration_seconds: int = 300, count: int = 5) -> dict:
    """Scroll the home feed and open notes until the time or count limit is reached."""
    if duration_seconds < 1:
        raise ValueError("浏览时间必须大于 0")
    if count < 1:
        raise ValueError("点开数量必须大于 0")

    started = time.monotonic()
    deadline = started + duration_seconds
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()

    completed: list[dict] = []
    seen: set[str] = set()
    empty_scrolls = 0

    while len(completed) < count and time.monotonic() < deadline:
        candidates = _browse_candidates(page, seen)
        if not candidates:
            viewport = int(page.evaluate("window.innerHeight || 768") or 768)
            page.scroll_by(0, int(viewport * random.uniform(0.7, 0.95)))
            time.sleep(min(1.5, max(0.0, deadline - time.monotonic())))
            empty_scrolls += 1
            if empty_scrolls >= 8:
                break
            continue

        empty_scrolls = 0
        feed = random.choice(candidates)
        seen.add(feed.id)
        _open_from_feed(page, feed)

        remaining_seconds = max(0.0, deadline - time.monotonic())
        remaining_items = max(1, count - len(completed))
        allocated_seconds = remaining_seconds / remaining_items
        read_seconds = _simulate_reading(page, allocated_seconds, deadline)
        close_method = _close_detail(page, feed.id)
        completed.append(
            {
                "feed_id": feed.id,
                "title": feed.note_card.display_title,
                "author": feed.note_card.user.nickname or feed.note_card.user.nick_name,
                "type": feed.note_card.type,
                "read_seconds": read_seconds,
                "closed_by": close_method,
            }
        )

        if len(completed) < count and time.monotonic() < deadline:
            viewport = int(page.evaluate("window.innerHeight || 768") or 768)
            page.scroll_by(0, int(viewport * random.uniform(0.65, 0.95)))

    elapsed = round(max(0.0, time.monotonic() - started), 2)
    if len(completed) >= count:
        stop_reason = "count_reached"
    elif time.monotonic() >= deadline:
        stop_reason = "time_limit"
    else:
        stop_reason = "no_more_feeds"
    reason_label = {
        "count_reached": "已达到点开数量",
        "time_limit": "已达到浏览时间",
        "no_more_feeds": "当前页面没有更多可浏览笔记",
    }[stop_reason]
    return {
        "success": True,
        "message": f"自动浏览完成，共点开 {len(completed)} 篇；{reason_label}",
        "count": len(completed),
        "requested_count": count,
        "duration_seconds": duration_seconds,
        "elapsed_seconds": elapsed,
        "stop_reason": stop_reason,
        "refreshed_between_items": False,
        "items": completed,
    }


def browse_like_cycle(
    page,
    count: int = 3,
    min_interval: float = 10.0,
    max_interval: float = 20.0,
    video_only: bool = True,
) -> dict:
    """Open, like, close, and scroll through feed items without refreshing between items."""
    if count < 1:
        raise ValueError("count 必须大于 0")
    if min_interval < 0 or max_interval < min_interval:
        raise ValueError("点赞间隔参数无效")

    # This is the only top-level navigation in the workflow.
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    time.sleep(random.uniform(0.8, 1.4))

    selected = _select_candidates(page, count, video_only)
    completed: list[dict] = []

    for position, feed in enumerate(selected):
        _open_from_feed(page, feed)
        page.simulate_reading_mouse(random.randint(2200, 4200))

        like_result = like_current_feed(page, feed.id)
        close_method = _close_detail(page, feed.id)

        item = {
            "feed_id": feed.id,
            "title": feed.note_card.display_title,
            "type": feed.note_card.type,
            "liked": like_result.success,
            "message": like_result.message,
            "closed_by": close_method,
        }

        if position < len(selected) - 1:
            viewport = int(page.evaluate("window.innerHeight || 768") or 768)
            scroll_distance = int(viewport * random.uniform(0.65, 0.95))
            page.scroll_by(0, scroll_distance)
            interval = random.uniform(min_interval, max_interval)
            item["scroll_distance"] = scroll_distance
            item["interval_after"] = round(interval, 2)
            time.sleep(interval)

        completed.append(item)

    return {
        "success": True,
        "count": len(completed),
        "video_only": video_only,
        "refreshed_between_items": False,
        "items": completed,
    }
