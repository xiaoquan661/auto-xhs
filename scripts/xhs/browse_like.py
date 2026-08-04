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
