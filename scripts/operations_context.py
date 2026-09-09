"""Account-scoped history and observed navigation targets for operations views."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import quote, urlencode


def platform_origin(platform: str) -> str:
    return "https://www.rednote.com" if platform == "rednote" else "https://www.xiaohongshu.com"


def event_location(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    origin = platform_origin(str(event.get("platform") or "xiaohongshu"))
    user = str(event.get("actor_user_id") or payload.get("user_id") or "")
    kind = event.get("event_type")
    feed = str(payload.get("feed_id") or event.get("object_id") or "")
    comment = str(payload.get("comment_id") or "")
    observed = str(payload.get("target_url") or "")
    thread = str(payload.get("thread_url") or "")
    scope = str(payload.get("location_scope") or "")
    if kind == "private_message":
        thread = thread or (f"{origin}/chat/{quote(user, safe='')}" if user else "")
        target, scope, label = thread, "conversation", "打开会话"
    elif kind in {"note_comment", "comment_reply"}:
        target = observed
        if not target and feed:
            target = f"{origin}/explore/{quote(feed, safe='')}"
            if payload.get("xsec_token"):
                target += "?" + urlencode(
                    {"xsec_token": payload["xsec_token"], "xsec_source": "pc_feed"}
                )
        scope = scope or "note"
        label = "查看评论位置" if scope == "comment" else "打开笔记并查看评论"
    else:
        target, label = observed, "查看用户主页"
    return {
        "target_url": target,
        "target_label": label,
        "thread_url": thread,
        "profile_url": str(payload.get("profile_url") or "")
        or (f"{origin}/user/profile/{quote(user, safe='')}" if user else ""),
        "location_scope": scope,
        "comment_id": comment,
        "location_hint": (
            f"评论者：{payload.get('nickname') or user}；评论：{payload.get('content') or ''}"
            if comment and scope != "comment"
            else ""
        ),
        "platform_read_status": payload.get("read_status") or "UNKNOWN",
        "handling_state": event.get("handling_state") or "NEW",
    }


def observed_post_date(value: Any) -> str:
    if not value:
        return ""
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            from datetime import timedelta, timezone

            epoch = float(value)
            if epoch > 10_000_000_000:
                epoch /= 1000
            return datetime.fromtimestamp(epoch, timezone(timedelta(hours=8))).date().isoformat()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return ""


def warm_candidates(events: list[dict], results: list[dict]) -> list[dict]:
    """Retain cross-day interaction evidence and exclude confirmed followed targets."""
    followed: set[str] = set()
    by_user: dict[str, dict] = {}
    observations = list(events)
    for result in results:
        body = result.get("business_result") or {}
        capability = result.get("capability") or (result.get("task_request") or {}).get(
            "capability"
        )
        if capability == "follow-user" and body.get("success") is True:
            target = body.get("readback") or body.get("target") or {}
            user = target.get("user_id") or (result.get("task_request") or {}).get(
                "parameters", {}
            ).get("user_id")
            if user:
                followed.add(str(user))
        if capability == "random-comment":
            source_id = result.get("record_id") or result.get("task_id")
            for index, item in enumerate(result.get("item_results") or body.get("items") or []):
                if item.get("success") is True and item.get("author_user_id"):
                    observations.append(
                        {
                            "event_id": f"{source_id}:{index}",
                            "event_type": "note_comment",
                            "actor_user_id": item["author_user_id"],
                            "platform": result.get("platform")
                            or (result.get("task_request") or {}).get("platform"),
                            "occurred_at": result.get("completed_at") or result.get("recorded_at"),
                            "payload": {
                                "nickname": item.get("author"),
                                "content": item.get("content"),
                            },
                        }
                    )
    for event in observations:
        if event.get("event_type") not in {"note_comment", "comment_reply", "private_message"}:
            continue
        user = str(event.get("actor_user_id") or "")
        if not user or user in followed:
            continue
        payload = event.get("payload") or {}
        entry = by_user.setdefault(
            user,
            {
                "user_id": user,
                "nickname": payload.get("nickname") or "",
                "profile_url": event_location(event)["profile_url"],
                "last_interaction_at": "",
                "evidence": [],
            },
        )
        entry["last_interaction_at"] = max(
            entry["last_interaction_at"], str(event.get("occurred_at") or "")
        )
        entry["evidence"].append(
            {
                "event_id": event.get("event_id") or event.get("platform_event_id"),
                "event_type": event["event_type"],
                "occurred_at": event.get("occurred_at"),
            }
        )
    return sorted(by_user.values(), key=lambda row: row["last_interaction_at"], reverse=True)
