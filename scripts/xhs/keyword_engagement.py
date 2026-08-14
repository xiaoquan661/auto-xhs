"""Search notes by keyword, sample candidates, then like or favorite them."""

from __future__ import annotations

import random
from collections.abc import Sequence

from .like_favorite import favorite_feed, like_feed
from .search import collect_search_feeds
from .types import Feed, FilterOption

_ACTIONS = {"like", "favorite", "both"}


def _requested_actions(action: str) -> tuple[str, ...]:
    return ("like", "favorite") if action == "both" else (action,)


def _candidate_needs_action(
    feed: Feed,
    action: str,
    excluded: dict[str, set[str]],
) -> bool:
    interact = feed.note_card.interact_info
    for action_name in _requested_actions(action):
        already_active = interact.liked if action_name == "like" else interact.collected
        if not already_active and feed.id not in excluded.get(action_name, set()):
            return True
    return False


def _item_base(feed: Feed) -> dict:
    return {
        "feed_id": feed.id,
        "title": feed.note_card.display_title,
        "author": feed.note_card.user.nickname or feed.note_card.user.nick_name,
        "xsec_token": feed.xsec_token,
        "actions": {},
    }


def keyword_engagement(
    page,
    *,
    keyword: str,
    action: str,
    count: int,
    candidate_pool_size: int = 20,
    collection_duration_seconds: int = 120,
    excluded_by_action: dict[str, Sequence[str]] | None = None,
    sampler=random.sample,
) -> dict:
    """Search, randomly select note candidates, and perform requested actions.

    ``count`` is the number of notes, not the number of button clicks. Notes
    already in the requested state are excluded before sampling.
    """

    keyword = keyword.strip()
    if not keyword:
        raise ValueError("关键词不能为空")
    if action not in _ACTIONS:
        raise ValueError("互动方式必须是 like、favorite 或 both")
    if count < 1:
        raise ValueError("随机互动数量必须大于 0")
    if candidate_pool_size < count:
        raise ValueError("候选池数量不能小于随机互动数量")
    if collection_duration_seconds < 1:
        raise ValueError("最长搜集时间必须大于 0")

    excluded = {
        name: {str(feed_id) for feed_id in values}
        for name, values in (excluded_by_action or {}).items()
    }
    candidates, collection = collect_search_feeds(
        page,
        keyword,
        FilterOption(),
        target_count=candidate_pool_size,
        duration_seconds=collection_duration_seconds,
        accept=lambda feed: (
            feed.model_type == "note"
            and bool(feed.id)
            and bool(feed.xsec_token)
            and _candidate_needs_action(feed, action, excluded)
        ),
    )
    selected = sampler(candidates, min(count, len(candidates))) if candidates else []
    items: list[dict] = []

    for feed in selected:
        item = _item_base(feed)
        interact = feed.note_card.interact_info
        requested_actions = _requested_actions(action)
        for action_name in requested_actions:
            already_active = (
                interact.liked if action_name == "like" else interact.collected
            )
            recently_executed = feed.id in excluded.get(action_name, set())
            if already_active or recently_executed:
                item["actions"][action_name] = {
                    "status": "skipped",
                    "message": "已处于目标状态" if already_active else "近期已执行，已跳过",
                }
                continue
            try:
                function = like_feed if action_name == "like" else favorite_feed
                result = function(page, feed.id, feed.xsec_token)
                item["actions"][action_name] = {
                    "status": "success" if result.success else "failed",
                    "message": result.message,
                }
            except Exception as exc:
                item["actions"][action_name] = {
                    "status": "failed",
                    "message": str(exc),
                }
        item["success"] = all(
            result["status"] in {"success", "skipped"}
            for result in item["actions"].values()
        )
        items.append(item)

    succeeded = sum(bool(item["success"]) for item in items)
    failed = len(items) - succeeded
    shortage = len(selected) < count
    partial = failed > 0 or shortage
    if not selected:
        message = "没有找到符合条件且尚未完成该互动的笔记"
    elif partial:
        message = f"随机互动部分完成：成功 {succeeded} 篇，失败 {failed} 篇，候选不足 {count - len(selected)} 篇"
    else:
        message = f"随机互动完成，共处理 {succeeded} 篇笔记"
    return {
        "success": not partial,
        "partial": partial,
        "result_type": "keyword_engagement",
        "message": message,
        "keyword": keyword,
        "action": action,
        "requested_count": count,
        "candidate_count": len(candidates),
        "candidate_pool_size": candidate_pool_size,
        **collection,
        "count": len(items),
        "succeeded_count": succeeded,
        "failed_count": failed,
        "items": items,
    }
