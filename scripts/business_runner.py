"""Registered V1 business executor using the existing XHS adapters."""

from __future__ import annotations

import threading
from collections.abc import Callable

from account_manager import load_account
from run_lock import for_account
from service_errors import ServiceError
from xhs.bridge import BridgePage


def _serialize(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return value
    return {"result": str(value)}


class BusinessRunner:
    def __init__(
        self,
        executor: Callable[[str, str, dict], dict] | None = None,
        *,
        max_concurrency: int = 3,
    ) -> None:
        self._executor = executor or self._execute_existing_adapter
        self.max_concurrency = max_concurrency
        self._semaphore = threading.BoundedSemaphore(max_concurrency)

    def execute(self, account: str, capability: str, parameters: dict) -> dict:
        if not self._semaphore.acquire(timeout=30):
            raise ServiceError("GLOBAL_BUSY", "当前并行任务已达到上限", 409)
        lock = for_account(account)
        try:
            if not lock.acquire(timeout=30):
                raise ServiceError("ACCOUNT_BUSY", "该账号正在执行其他任务", 409)
            return self._executor(account, capability, parameters)
        finally:
            lock.release()
            self._semaphore.release()

    @staticmethod
    def _execute_existing_adapter(account: str, capability: str, parameters: dict) -> dict:
        config = load_account(account, allow_legacy_default=False)
        page = BridgePage(
            config.bridge_url,
            account=config.name,
            account_id=config.account_id,
            bridge_token=config.bridge_token,
        )
        if capability == "list-feeds":
            from xhs.feeds import list_feeds

            feeds = list_feeds(page)
            return {"feeds": _serialize(feeds), "count": len(feeds)}
        if capability == "search-feeds":
            from xhs.search import search_feeds
            from xhs.types import FilterOption

            feeds = search_feeds(page, str(parameters.get("keyword") or ""), FilterOption())
            return {"feeds": _serialize(feeds), "count": len(feeds)}
        if capability == "get-feed-detail":
            from xhs.feed_detail import get_feed_detail

            return _serialize(
                get_feed_detail(
                    page,
                    str(parameters.get("feed_id") or ""),
                    str(parameters.get("xsec_token") or ""),
                )
            )
        if capability == "user-profile":
            from xhs.user_profile import get_user_profile

            return _serialize(
                get_user_profile(
                    page,
                    str(parameters.get("user_id") or ""),
                    str(parameters.get("xsec_token") or ""),
                )
            )
        if capability in {"like-feed", "favorite-feed"}:
            from xhs.like_favorite import (
                favorite_feed,
                like_feed,
                unfavorite_feed,
                unlike_feed,
            )

            feed_id = str(parameters.get("feed_id") or "")
            token = str(parameters.get("xsec_token") or "")
            undo = bool(parameters.get("undo"))
            function = {
                ("like-feed", False): like_feed,
                ("like-feed", True): unlike_feed,
                ("favorite-feed", False): favorite_feed,
                ("favorite-feed", True): unfavorite_feed,
            }[(capability, undo)]
            result = function(page, feed_id, token)
            if not result.success:
                raise ServiceError("ACTION_FAILED", result.message, 409)
            return result.to_dict()
        if capability in {"post-comment", "reply-comment"}:
            from xhs.comment import post_comment, reply_comment

            feed_id = str(parameters.get("feed_id") or "")
            token = str(parameters.get("xsec_token") or "")
            content = str(parameters.get("content") or "")
            if capability == "post-comment":
                post_comment(page, feed_id, token, content)
                return {"success": True, "message": "评论发送成功"}
            reply_comment(
                page,
                feed_id,
                token,
                content,
                comment_id=str(parameters.get("comment_id") or ""),
                user_id=str(parameters.get("user_id") or ""),
            )
            return {"success": True, "message": "回复发送成功"}
        raise ServiceError("CAPABILITY_DISABLED", "该能力没有 V1 执行适配器", 409)
