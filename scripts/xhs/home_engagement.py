"""Browse and like notes within one recommendation-feed page session."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence

from .browse_like import _browse_candidates, _close_detail, _open_from_feed, _simulate_reading
from .errors import XHSError
from .like_favorite import like_current_feed
from .progress import emit_progress
from .types import Feed
from .urls import HOME_URL


def _feed_item(feed: Feed) -> dict:
    return {
        "feed_id": feed.id,
        "xsec_token": feed.xsec_token,
        "title": feed.note_card.display_title,
        "author": feed.note_card.user.nickname or feed.note_card.user.nick_name,
        "type": feed.note_card.type,
        "read_seconds": 0.0,
        "closed_by": "",
        "browse": {"status": "success", "message": "详情页已打开"},
        "like": None,
    }


def home_engagement(
    page,
    *,
    browse_count: int = 6,
    like_count: int = 2,
    video_only: bool = False,
    excluded_feed_ids: Sequence[str] = (),
    excluded_user_ids: Sequence[str] = (),
    duration_seconds: int = 600,
    min_read_seconds: float = 15.0,
    max_read_seconds: float = 45.0,
    chooser: Callable[[Sequence[Feed]], Feed] = random.choice,
    reader: Callable = _simulate_reading,
    liker: Callable = like_current_feed,
) -> dict:
    """Complete one home-feed browse-and-like batch without reopening the home page."""
    if not 1 <= browse_count <= 50:
        raise ValueError("浏览数量必须在 1 到 50 篇之间")
    if not 0 <= like_count <= browse_count:
        raise ValueError("点赞数量必须在 0 到浏览数量之间")
    if duration_seconds < 1:
        raise ValueError("最长执行时间必须大于 0")
    if min_read_seconds < 0 or max_read_seconds < min_read_seconds:
        raise ValueError("单篇阅读时间参数无效")

    started = time.monotonic()
    deadline = started + duration_seconds
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()

    seen = {str(feed_id).strip() for feed_id in excluded_feed_ids if str(feed_id).strip()}
    excluded_feed_count = len(seen)
    items: list[dict] = []
    skipped: list[dict] = []
    liked_count = 0
    next_like_at = 0.0
    intervening_browses = 2
    result_unknown = False
    stopped_by = ""
    empty_scrolls = 0

    while len(items) < browse_count and time.monotonic() < deadline:
        candidates = _browse_candidates(page, seen)
        own = [feed for feed in candidates if feed.note_card.user.user_id in excluded_user_ids]
        seen.update(feed.id for feed in own)
        candidates = [feed for feed in candidates if feed not in own]
        if video_only:
            video_candidates = [
                feed for feed in candidates if str(feed.note_card.type).casefold() == "video"
            ]
            seen.update(feed.id for feed in candidates if feed not in video_candidates)
            candidates = video_candidates
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

        like_due = (
            liked_count < like_count
            and intervening_browses >= 2
            and not feed.note_card.interact_info.liked
        )
        if like_due and next_like_at < deadline:
            wait_seconds = max(0.0, next_like_at - time.monotonic())
            if wait_seconds:
                emit_progress("waiting", "等待下一次点赞间隔", feed_id=feed.id)
                time.sleep(wait_seconds)
        if like_due and next_like_at <= time.monotonic() < deadline:
            intervening_browses = 0
            next_like_at = time.monotonic() + random.uniform(90.0, 180.0)
            try:
                result = liker(page, feed.id)
                changed = result.changed is not False
                item["like"] = {
                    "status": "success"
                    if result.success and changed
                    else "skipped"
                    if result.success
                    else "failed",
                    "message": result.message,
                    "success": bool(result.success),
                    "changed": changed,
                    "platform_readback": bool(result.success),
                }
                if result.success and changed:
                    liked_count += 1
            except Exception as exc:
                result_unknown = bool(getattr(exc, "result_unknown", True))
                item["like"] = {
                    "status": "result_unknown" if result_unknown else "failed",
                    "message": str(exc),
                    "success": False,
                    "platform_readback": False,
                }
                stopped_by = "like_result_unknown" if result_unknown else "like_state_unavailable"
        else:
            intervening_browses += 1

        try:
            item["closed_by"] = _close_detail(page, feed.id)
        except XHSError as exc:
            item["browse"] = {"status": "failed", "message": str(exc)}
            items.append(item)
            break
        items.append(item)

        emit_progress(
            "browsing",
            f"已浏览 {len(items)}/{browse_count} 篇，点赞 {liked_count}/{like_count} 篇",
            metrics={
                "browse": {"actual": len(items), "target": browse_count},
                "like": {"actual": liked_count, "target": like_count},
            },
            feed_id=feed.id,
            title=item["title"],
            item_index=len(items),
        )

        if stopped_by:
            break

        if len(items) < browse_count and time.monotonic() < deadline:
            viewport = int(page.evaluate("window.innerHeight || 768") or 768)
            page.scroll_by(0, int(viewport * random.uniform(0.65, 0.95)))

    browsed_count = len(items)
    partial = (
        browsed_count < browse_count
        or liked_count < like_count
        or any(item["browse"]["status"] != "success" for item in items)
    )
    elapsed = round(max(0.0, time.monotonic() - started), 2)
    if stopped_by:
        stop_reason = stopped_by
    elif browsed_count >= browse_count and liked_count >= like_count:
        stop_reason = "count_reached"
    elif time.monotonic() >= deadline:
        stop_reason = "time_limit"
    else:
        stop_reason = "browse_limit" if browsed_count >= browse_count else "no_more_feeds"
    message = (
        f"首页浏览点赞完成：浏览 {browsed_count} 篇，点赞 {liked_count} 篇"
        if not partial
        else (
            f"首页浏览点赞部分完成：浏览 {browsed_count}/{browse_count} 篇，"
            f"点赞 {liked_count}/{like_count} 篇"
        )
    )
    return {
        "success": not partial,
        "partial": partial,
        "result_unknown": result_unknown,
        "result_type": "home_engagement",
        "message": message,
        "requested": {
            "browse_count": browse_count,
            "like_count": like_count,
            "video_only": video_only,
            "excluded_feed_count": excluded_feed_count,
            "duration_seconds": duration_seconds,
            "min_read_seconds": min_read_seconds,
            "max_read_seconds": max_read_seconds,
            "like_interval_seconds": [90, 180],
            "min_intervening_browses": 2,
        },
        "counts": {
            "browsed": browsed_count,
            "liked": liked_count,
            "skipped": len(skipped),
        },
        "elapsed_seconds": elapsed,
        "stop_reason": stop_reason,
        "refreshed_between_items": False,
        "items": items,
        "skipped_items": skipped,
    }
