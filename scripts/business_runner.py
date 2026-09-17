"""Registered V1 business executor using the existing XHS adapters."""

from __future__ import annotations

import threading
from collections.abc import Callable

from account_manager import account_dir, load_account
from platforms import CapabilitySupport, get_platform_capability, normalize_platform
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
            from operations_policy import check_daily_context

            check_daily_context(parameters, account=account)
            return self._executor(account, capability, parameters)
        finally:
            lock.release()
            self._semaphore.release()

    @staticmethod
    def _execute_existing_adapter(account: str, capability: str, parameters: dict) -> dict:
        config = load_account(account, allow_legacy_default=False)
        requested_platform = normalize_platform(parameters.get("platform") or config.platform)
        if requested_platform != config.platform:
            raise ServiceError(
                "PLATFORM_MISMATCH",
                "任务平台与账号槽位平台不一致",
                409,
            )
        support = get_platform_capability(config.platform, capability)
        if support in {CapabilitySupport.UNKNOWN, CapabilitySupport.UNSUPPORTED}:
            raise ServiceError(
                "PLATFORM_CAPABILITY_UNAVAILABLE",
                f"{capability} 在 {config.platform} 上当前状态为 {support.value}",
                409,
            )
        page = BridgePage(
            config.bridge_url,
            account=config.name,
            account_id=config.account_id,
            bridge_token=config.bridge_token,
            platform=config.platform,
        )
        if capability == "list-feeds":
            from xhs.feeds import list_feeds

            feeds = list_feeds(page)
            return {"feeds": _serialize(feeds), "count": len(feeds)}
        if capability == "browse-feeds":
            from xhs.browse_like import browse_feed_cycle

            return browse_feed_cycle(
                page,
                duration_seconds=int(parameters.get("duration_minutes") or 5) * 60,
                count=int(parameters.get("count") or 5),
            )
        if capability == "search-feeds":
            from xhs.search import search_feeds
            from xhs.types import FilterOption

            feeds = search_feeds(page, str(parameters.get("keyword") or ""), FilterOption())
            return {"feeds": _serialize(feeds), "count": len(feeds)}
        if capability == "keyword-engagement":
            from xhs.keyword_engagement import keyword_engagement

            return keyword_engagement(
                page,
                keyword=str(parameters.get("keyword") or ""),
                action=str(parameters.get("action") or ""),
                count=int(parameters.get("count") or 0),
                candidate_pool_size=int(parameters.get("candidate_pool_size") or 20),
                collection_duration_seconds=int(parameters.get("collection_minutes") or 2) * 60,
                excluded_by_action=parameters.get("excluded_by_action") or {},
            )
        if capability == "random-comment":
            from memory_comment import MemoryCommentGenerator
            from xhs.random_comment import random_comment

            context = parameters.get("memory_context") or {}
            generator = MemoryCommentGenerator(context) if context.get("identity", {}).get("uid") else None
            result = random_comment(
                page,
                count=int(parameters.get("count") or 1),
                candidate_pool_size=int(parameters.get("candidate_pool_size") or 20),
                collection_duration_seconds=int(parameters.get("collection_minutes") or 2) * 60,
                style=str(parameters.get("style") or "natural"),
                excluded_feed_ids=parameters.get("excluded_feed_ids") or [],
                excluded_user_ids=parameters.get("excluded_user_ids") or [],
                content_generator=generator,
            )
            if generator:
                result["memory_usages"] = generator.usages
                result["adopted_memory_refs"] = list(dict.fromkeys(
                    ref for usage in generator.usages for ref in usage["adopted_refs"]
                ))
            return result
        if capability == "home-engagement":
            from xhs.home_engagement import home_engagement

            return home_engagement(
                page,
                browse_count=int(parameters.get("browse_count") or 6),
                like_count=int(parameters.get("like_count") or 0),
                video_only=parameters.get("video_only") is True,
                excluded_feed_ids=parameters.get("excluded_feed_ids") or [],
                excluded_user_ids=parameters.get("excluded_user_ids") or [],
                duration_seconds=int(parameters.get("duration_minutes") or 10) * 60,
                min_read_seconds=float(parameters.get("min_read_seconds", 15)),
                max_read_seconds=float(parameters.get("max_read_seconds", 45)),
                pacing_path=account_dir(account) / "engagement-pacing.json",
            )
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
        if capability in {"follow-user-preview", "follow-user"}:
            from xhs.follow import follow_user, inspect_follow_target

            user_id = str(parameters.get("user_id") or "")
            xsec_token = str(parameters.get("xsec_token") or "")
            if capability == "follow-user-preview":
                return inspect_follow_target(page, user_id, xsec_token)
            return follow_user(page, user_id, xsec_token)
        if capability == "private-message-context":
            from xhs.direct_message import inspect_private_message_context

            return inspect_private_message_context(
                page,
                str(parameters.get("user_id") or ""),
                str(parameters.get("xsec_token") or ""),
                limit=int(parameters.get("limit") or 10),
            )
        if capability == "collect-private-messages":
            from xhs.private_inbox import collect_private_messages

            return collect_private_messages(
                page,
                max_conversations=int(parameters.get("max_conversations") or 20),
                context_limit=int(parameters.get("context_limit") or 10),
            )
        if capability == "send-private-messages":
            from xhs.direct_message import send_private_message

            return send_private_message(
                page,
                str(parameters.get("user_id") or ""),
                str(parameters.get("xsec_token") or ""),
                str(parameters.get("content") or ""),
                expected_nickname=str(parameters.get("nickname") or ""),
                expected_conversation_type=str(parameters.get("conversation_type") or ""),
                expected_peer_turn=(
                    list(parameters.get("expected_peer_turn") or [])
                    if "expected_peer_turn" in parameters
                    else None
                ),
            )
        if capability == "collect-note-comments":
            from xhs.comment_monitor import collect_note_comments
            from xhs.notifications import collect_comment_notifications

            tracked_notes = list(parameters.get("tracked_notes") or [])
            owner_user_id = str(parameters.get("owner_user_id") or "")
            if tracked_notes:
                return collect_note_comments(
                    page,
                    tracked_notes,
                    owner_user_id=owner_user_id,
                )
            return collect_comment_notifications(
                page,
                max_items=int(parameters.get("max_notes") or 20),
            )
        if capability == "collect-operations-metrics":
            if parameters.get("source") == "notification_history":
                from xhs.growth_notifications import collect_growth_notifications

                growth = collect_growth_notifications(page)
                return {
                    "source": "notification_history",
                    "captured_at": growth["captured_at"],
                    "growth_notifications": growth,
                    "partial": bool(growth.get("partial")),
                }
            if parameters.get("source") == "creator_manager":
                from xhs.creator_metrics import collect_creator_metrics

                return collect_creator_metrics(
                    page, note_ids=parameters.get("note_ids") or [],
                    max_notes=int(parameters.get("max_notes") or 50),
                )
            from xhs.growth_notifications import collect_growth_notifications
            from xhs.metrics_collector import collect_operations_metrics

            metrics = collect_operations_metrics(
                page,
                owner_user_id=str(parameters.get("owner_user_id") or ""),
                owner_xsec_token=str(parameters.get("owner_xsec_token") or ""),
                max_notes=int(parameters.get("max_notes") or 200),
            )
            if parameters.get("collect_growth", True):
                try:
                    metrics["growth_notifications"] = collect_growth_notifications(page)
                except Exception as exc:
                    metrics["growth_notifications"] = {
                        "events": [],
                        "partial": True,
                        "error": str(exc),
                    }
            return metrics
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
            from xhs.notifications import reply_to_comment_notification

            feed_id = str(parameters.get("feed_id") or "")
            token = str(parameters.get("xsec_token") or "")
            content = str(parameters.get("content") or "")
            if capability == "post-comment":
                post_comment(page, feed_id, token, content)
                return {"success": True, "message": "评论发送成功"}
            original_content = str(parameters.get("original_content") or "")
            nickname = str(parameters.get("nickname") or "")
            notification_id = str(parameters.get("notification_id") or "")
            if original_content or notification_id:
                return reply_to_comment_notification(
                    page,
                    content,
                    notification_id=notification_id,
                    comment_id=str(parameters.get("comment_id") or ""),
                    nickname=nickname,
                    original_content=original_content,
                )
            reply_comment(
                page,
                feed_id,
                token,
                content,
                comment_id=str(parameters.get("comment_id") or ""),
                user_id=str(parameters.get("user_id") or ""),
            )
            return {"success": True, "message": "回复发送成功"}
        if capability in {"fill-publish", "fill-publish-video"}:
            from xhs.types import PublishImageContent, PublishVideoContent

            preview = parameters["preview"]
            common = {
                "title": preview["title"],
                "content": preview["content"],
                "tags": list(preview.get("tags") or []),
                "schedule_time": preview.get("schedule_at"),
                "visibility": preview.get("visibility") or "公开可见",
            }
            if capability == "fill-publish":
                from xhs.publish import fill_publish_form

                fill_publish_form(
                    page,
                    PublishImageContent(
                        **common,
                        image_paths=preview["assets"],
                        is_original=bool(preview.get("original")),
                    ),
                )
            else:
                from xhs.publish_video import fill_publish_video_form

                fill_publish_video_form(
                    page, PublishVideoContent(**common, video_path=preview["assets"][0])
                )
            return {"success": True, "published": False, "preview": preview}
        if capability == "click-publish":
            from xhs.publish import click_publish_button

            return click_publish_button(
                page,
                expected_title=str(parameters.get("expected_title") or ""),
            )
        if capability == "save-draft":
            from xhs.publish import save_as_draft

            return save_as_draft(
                page,
                expected_title=str(parameters.get("expected_title") or ""),
            )
        raise ServiceError("CAPABILITY_DISABLED", "该能力没有 V1 执行适配器", 409)
