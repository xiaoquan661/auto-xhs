"""Collect home-feed candidates, generate related comments, and post them."""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable, Sequence

from .comment import post_comment
from .feed_detail import get_feed_detail
from .feeds import extract_current_feeds
from .types import Feed, FeedDetail
from .urls import HOME_URL

_COMMENT_STYLES = {"natural", "praise", "question"}


def _clean_text(value: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", value or "").strip(" ，。！？!?；;：:")
    return f"{text[:limit]}…" if len(text) > limit else text


def _content_focus(note: FeedDetail) -> str:
    body = _clean_text(note.body or note.desc, 120)
    if not body:
        return ""
    sentences = [
        _clean_text(item, 28)
        for item in re.split(r"[。！？!?；;\n]", body)
        if len(_clean_text(item, 28)) >= 6
    ]
    return sentences[0] if sentences else _clean_text(body, 28)


def generate_comment_content(
    note: FeedDetail,
    *,
    style: str = "natural",
    chooser: Callable[[Sequence[str]], str] = random.choice,
) -> str:
    """Create a short comment grounded in the note title and body."""

    if style not in _COMMENT_STYLES:
        raise ValueError("评论风格必须是 natural、praise 或 question")
    title = _clean_text(note.title, 28) or "这篇内容"
    focus = _content_focus(note)

    if style == "praise":
        choices = [
            f"这篇关于「{title}」的分享整理得很清楚，很有参考价值。",
            f"「{title}」这个角度很有启发，感谢认真整理。",
        ]
        if focus:
            choices.append(f"「{title}」讲得很清楚，尤其是“{focus}”这一点，很有启发。")
    elif style == "question":
        choices = [f"关于「{title}」，实际操作时最需要注意的是什么？"]
        if focus:
            choices.append(f"关于「{title}」，想请教一下“{focus}”在实际操作中还要注意什么？")
    else:
        choices = [
            f"看完对「{title}」有了更具体的理解，感谢分享。",
            f"「{title}」这个角度很有启发，内容也整理得很清楚。",
        ]
        if focus:
            choices.append(f"这篇「{title}」很有参考价值，“{focus}”这个细节讲得很清楚。")
    return chooser(choices)


def collect_home_feeds(
    page,
    *,
    target_count: int = 20,
    duration_seconds: int = 120,
    accept: Callable[[Feed], bool] | None = None,
    max_empty_scrolls: int = 6,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[list[Feed], dict]:
    """Collect a deduplicated candidate pool from the recommendation feed."""

    if target_count < 1:
        raise ValueError("候选池数量必须大于 0")
    if duration_seconds < 1:
        raise ValueError("搜集时间必须大于 0")

    started = time.monotonic()
    deadline = started + duration_seconds
    page.navigate(HOME_URL)
    page.wait_for_load()
    page.wait_dom_stable()

    seen: set[str] = set()
    collected: dict[str, Feed] = {}

    def add_current() -> int:
        added = 0
        for feed in extract_current_feeds(page):
            if not feed.id or feed.id in seen:
                continue
            seen.add(feed.id)
            if accept is not None and not accept(feed):
                continue
            collected[feed.id] = feed
            added += 1
        return added

    add_current()
    scroll_count = 0
    empty_scrolls = 0
    while len(collected) < target_count and time.monotonic() < deadline:
        viewport = int(page.evaluate("window.innerHeight || 768") or 768)
        page.scroll_by(0, int(viewport * random.uniform(0.72, 0.96)))
        scroll_count += 1
        sleeper(min(random.uniform(0.9, 1.5), max(0.0, deadline - time.monotonic())))
        if add_current():
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


def random_comment(
    page,
    *,
    count: int,
    candidate_pool_size: int = 20,
    collection_duration_seconds: int = 120,
    style: str = "natural",
    excluded_feed_ids: Sequence[str] | None = None,
    sampler=random.sample,
    detail_reader=None,
    comment_sender=None,
    content_generator=None,
) -> dict:
    """Randomly select recommendation notes and directly post related comments."""

    if not 1 <= count <= 3:
        raise ValueError("随机评论数量必须在 1 到 3 篇之间")
    if not count <= candidate_pool_size <= 100:
        raise ValueError("候选池数量必须不少于评论数量，且不超过 100 篇")
    if collection_duration_seconds < 1:
        raise ValueError("最长搜集时间必须大于 0")
    if style not in _COMMENT_STYLES:
        raise ValueError("评论风格必须是 natural、praise 或 question")

    excluded = {str(feed_id) for feed_id in (excluded_feed_ids or [])}
    candidates, collection = collect_home_feeds(
        page,
        target_count=candidate_pool_size,
        duration_seconds=collection_duration_seconds,
        accept=lambda feed: (
            feed.model_type == "note"
            and bool(feed.id)
            and bool(feed.xsec_token)
            and feed.id not in excluded
        ),
    )
    selected = sampler(candidates, min(count, len(candidates))) if candidates else []
    read_detail = detail_reader or get_feed_detail
    send_comment = comment_sender or post_comment
    make_content = content_generator or generate_comment_content
    items: list[dict] = []

    for feed in selected:
        item = {
            "feed_id": feed.id,
            "title": feed.note_card.display_title,
            "author": feed.note_card.user.nickname or feed.note_card.user.nick_name,
            "content": "",
            "status": "failed",
            "message": "",
            "success": False,
        }
        try:
            detail = read_detail(page, feed.id, feed.xsec_token)
            item["title"] = detail.note.title or item["title"]
            item["author"] = detail.note.user.nickname or item["author"]
            item["content"] = make_content(detail.note, style=style)
            send_comment(page, feed.id, feed.xsec_token, item["content"])
            item.update(status="success", message="评论发送成功", success=True)
        except Exception as exc:
            item["message"] = str(exc)
        items.append(item)

    succeeded = sum(bool(item["success"]) for item in items)
    failed = len(items) - succeeded
    shortage = len(selected) < count
    partial = failed > 0 or shortage
    if not selected:
        message = "首页没有找到可评论的候选笔记"
    elif partial:
        message = f"随机评论部分完成：成功 {succeeded} 篇，失败 {failed} 篇，候选不足 {count - len(selected)} 篇"
    else:
        message = f"随机评论完成，共发送 {succeeded} 条评论"
    return {
        "success": not partial,
        "partial": partial,
        "result_type": "random_comment",
        "message": message,
        "style": style,
        "requested_count": count,
        "candidate_count": len(candidates),
        "candidate_pool_size": candidate_pool_size,
        **collection,
        "count": len(items),
        "succeeded_count": succeeded,
        "failed_count": failed,
        "items": items,
    }
