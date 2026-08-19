"""Validation and public previews for Agent-owned private-message batches."""

from __future__ import annotations

from service_errors import ServiceError

MAX_PRIVATE_MESSAGE_RECIPIENTS = 10


def normalize_private_message_recipients(recipients: list[dict]) -> list[dict]:
    items = list(recipients or [])
    if not 1 <= len(items) <= MAX_PRIVATE_MESSAGE_RECIPIENTS:
        raise ServiceError("INVALID_REQUEST", "单次私信任务必须包含 1 到 10 位收件人")

    normalized: list[dict] = []
    seen_users: set[str] = set()
    seen_contents: set[str] = set()
    for raw in items:
        user_id = str(raw.get("user_id") or "").strip()
        nickname = str(raw.get("nickname") or "").strip()
        xsec_token = str(raw.get("xsec_token") or "").strip()
        content = str(raw.get("content") or "").strip()
        if not user_id or not content:
            raise ServiceError("INVALID_REQUEST", "每条私信必须包含收件人 ID 和最终文本")
        if user_id in seen_users:
            raise ServiceError("INVALID_REQUEST", "同一私信任务不能重复包含同一收件人")
        if len(items) > 1 and content in seen_contents:
            raise ServiceError("PERSONALIZATION_REQUIRED", "每位收件人的私信文本必须不同", 409)
        seen_users.add(user_id)
        seen_contents.add(content)
        normalized.append(
            {
                "user_id": user_id,
                "nickname": nickname,
                "xsec_token": xsec_token,
                "content": content,
            }
        )
    return normalized


def private_message_preview(recipients: list[dict]) -> list[dict]:
    """Return the exact user-visible batch without exposing navigation tokens."""

    return [
        {
            "user_id": item["user_id"],
            "nickname": item.get("nickname") or "",
            "content": item["content"],
        }
        for item in recipients
    ]
