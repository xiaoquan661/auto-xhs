"""Browse, like, and comment within one recommendation-feed page session."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence

from .browse_like import _browse_candidates, _close_detail, _open_from_feed, _simulate_reading
from .comment import post_comment_current
from .errors import XHSError
from .like_favorite import like_current_feed
from .random_comment import generate_comment_content
from .types import Feed, FeedDetail
from .urls import HOME_URL

_COMMENT_STYLES = {"natural", "praise", "question"}


def _feed_item(feed: Feed) -> dict:
    return {
        "feed_id": feed.id,
        "title": feed.note_card.display_title,
        "author": feed.note_card.user.nickname or feed.note_card.user.nick_name,
        "type": feed.note_card.type,
        "read_seconds": 0.0,
        "closed_by": "",
        "browse": {"status": "success", "message": "详情页已打开"},
        "like": None,
        "comment": None,
    }


def home_engagement(
    page,
    *,
    browse_count: int = 6,
    like_count: int = 2,
    comment_count: int = 1,
    duration_seconds: int = 180,
    min_read_seconds: float = 8.0,
    max_read_seconds: float = 15.0,
    style: str = "natural",
    chooser: Callable[[Sequence[Feed]], Feed] = random.choice,
    reader: Callable = _simulate_reading,
    liker: Callable = like_current_feed,
    commenter: Callable = post_comment_current,
    comment_generator: Callable = generate_comment_content,
) -> dict:
    """Complete one home-feed engagement batch without reopening the home page."""
    if not 1 <= browse_count <= 50:
        raise ValueError("浏览数量必须在 1 到 50 篇之间")
    if not 0 <= like_count <= browse_count:
        raise ValueError("点赞数量必须在 0 到浏览数量之间")
    if not 0 <= comment_count <= min(3, browse_count):
        raise ValueError("评论数量必须在 0 到 3 篇之间，且不能超过浏览数量")
    if duration_seconds < 1:
        raise ValueError("最长执行时间必须大于 0")
    if min_read_seconds < 0 or max_read_seconds < min_read_seconds:
        raise ValueError("单篇阅读时间参数无效")
    if style not in _COMMENT_STYLES:
        raise ValueError("评论风格必须是 natural、praise 或 question")

    started = time.monotonic()
    deadline = started + duration_seconds
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()

    seen: set[str] = set()
    items: list[dict] = []
    skipped: list[dict] = []
    liked_count = 0
    commented_count = 0
    comment_attempted = False
    empty_scrolls = 0

    while len(items) < browse_count and time.monotonic() < deadline:
        candidates = _browse_candidates(page, seen)
        if not candidates:
            viewport = int(page.evaluate("window.innerHeight || 768") or 768)
            page.scroll_by(0, int(viewport * random.uniform(0.7, 0.95)))
            time.sleep(min(1.2, max(0.0, deadline - time.monotonic())))
            empty_scrolls += 1
            if empty_scrolls >= 8:
                break
            continue

        empty_scrolls = 0
        feed = chooser(candidates)
        seen.add(feed.id)
        try:
            _open_from_feed(page, feed)
        except XHSError as exc:
            skipped.append({"feed_id": feed.id, "status": "skipped", "message": str(exc)})
            continue

        item = _feed_item(feed)
        item["read_seconds"] = reader(
            page,
            random.uniform(min_read_seconds, max_read_seconds),
            deadline,
        )

        if liked_count < like_count and not feed.note_card.interact_info.liked:
            try:
                result = liker(page, feed.id)
                item["like"] = {
                    "status": "success" if result.success else "failed",
                    "message": result.message,
                    "success": bool(result.success),
                }
                if result.success:
                    liked_count += 1
            except Exception as exc:
                item["like"] = {"status": "failed", "message": str(exc), "success": False}

        if commented_count < comment_count and not comment_attempted and item["title"]:
            comment_attempted = True
            detail = FeedDetail(note_id=feed.id, title=item["title"], type=item["type"])
            content = comment_generator(detail, style=style)
            try:
                commenter(page, content, feed_id=feed.id)
                item["comment"] = {
                    "status": "success",
                    "message": "评论发送成功",
                    "content": content,
                    "success": True,
                }
                commented_count += 1
            except Exception as exc:
                item["comment"] = {
                    "status": "failed",
                    "message": str(exc),
                    "content": content,
                    "success": False,
                }

        try:
            item["closed_by"] = _close_detail(page, feed.id)
        except XHSError as exc:
            item["browse"] = {"status": "failed", "message": str(exc)}
            items.append(item)
            break
        items.append(item)

        if len(items) < browse_count and time.monotonic() < deadline:
            viewport = int(page.evaluate("window.innerHeight || 768") or 768)
            page.scroll_by(0, int(viewport * random.uniform(0.65, 0.95)))

    browsed_count = len(items)
    partial = (
        browsed_count < browse_count
        or liked_count < like_count
        or commented_count < comment_count
        or any(item["browse"]["status"] != "success" for item in items)
    )
    elapsed = round(max(0.0, time.monotonic() - started), 2)
    if browsed_count >= browse_count:
        stop_reason = "count_reached"
    elif time.monotonic() >= deadline:
        stop_reason = "time_limit"
    else:
        stop_reason = "no_more_feeds"
    message = (
        f"首页互动完成：浏览 {browsed_count} 篇，点赞 {liked_count} 篇，评论 {commented_count} 篇"
        if not partial
        else f"首页互动部分完成：浏览 {browsed_count}/{browse_count} 篇，点赞 {liked_count}/{like_count} 篇，评论 {commented_count}/{comment_count} 篇"
    )
    return {
        "success": not partial,
        "partial": partial,
        "result_type": "home_engagement",
        "message": message,
        "requested": {
            "browse_count": browse_count,
            "like_count": like_count,
            "comment_count": comment_count,
            "duration_seconds": duration_seconds,
            "min_read_seconds": min_read_seconds,
            "max_read_seconds": max_read_seconds,
            "style": style,
        },
        "counts": {
            "browsed": browsed_count,
            "liked": liked_count,
            "commented": commented_count,
            "skipped": len(skipped),
        },
        "elapsed_seconds": elapsed,
        "stop_reason": stop_reason,
        "refreshed_between_items": False,
        "items": items,
        "skipped_items": skipped,
    }
