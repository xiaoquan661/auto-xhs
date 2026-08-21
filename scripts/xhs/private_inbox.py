"""Collect latest incoming text messages from the Xiaohongshu web inbox."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from .direct_message import CHAT_URL, _read_chat


_READ_CONVERSATIONS_JS = r"""
(() => Array.from(document.querySelectorAll('.xhs-im-conv-item'))
    .filter((node) => node.getAttribute('data-conv-kind') === 'c2c')
    .map((node) => ({
        user_id: String(node.getAttribute('data-conv-id') || '').trim(),
        nickname: String(
            node.querySelector('.xhs-im-conv-item__name')?.innerText || ''
        ).trim(),
        display_time: String(
            node.querySelector('.xhs-im-conv-item__time')?.innerText || ''
        ).trim(),
        preview: String(
            node.querySelector('.xhs-im-conv-item__summary-text')?.innerText || ''
        ).trim()
    }))
    .filter((item) => item.user_id))()
"""

_PLATFORM_NOTICES = {
    "我们已相互关注，开始聊天吧",
    "你们已相互关注，开始聊天吧",
}


def collect_private_messages(
    page,
    *,
    max_conversations: int = 20,
    context_limit: int = 10,
) -> dict:
    """Read latest peer-authored text from visible one-to-one conversations."""

    max_conversations = max(1, min(int(max_conversations), 50))
    context_limit = max(1, min(int(context_limit), 20))
    page.navigate(CHAT_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    raw = page.evaluate(_READ_CONVERSATIONS_JS)
    conversations = list(raw)[:max_conversations] if isinstance(raw, list) else []

    messages: list[dict] = []
    skipped: list[dict] = []
    observed_at = datetime.now(UTC).isoformat()
    for conversation in conversations:
        user_id = str(conversation.get("user_id") or "").strip()
        if not _open_inbox_conversation(page, user_id):
            page.navigate(f"{CHAT_URL}/{user_id}")
        state = _wait_for_chat(
            page,
            user_id=user_id,
            expected_nickname=str(conversation.get("nickname") or ""),
        )
        if not state["available"]:
            skipped.append({"user_id": user_id, "reason": "conversation_unavailable"})
            continue
        recent = list(state["messages"])[-context_limit:]
        if not recent or recent[-1]["role"] != "peer":
            skipped.append({"user_id": user_id, "reason": "latest_message_not_peer"})
            continue
        content = str(recent[-1].get("content") or "").strip()
        if content in _PLATFORM_NOTICES:
            skipped.append({"user_id": user_id, "reason": "latest_message_platform_notice"})
            continue
        if not content or (content.startswith("[") and content.endswith("]")):
            skipped.append({"user_id": user_id, "reason": "latest_message_not_text"})
            continue
        nickname = str(state.get("target_nickname") or conversation.get("nickname") or "").strip()
        messages.append(
            {
                "message_id": f"{user_id}:{len(state['messages'])}:{content}",
                "user_id": user_id,
                "nickname": nickname,
                "content": content,
                "display_time": str(conversation.get("display_time") or ""),
                "occurred_at": observed_at,
                "message_count": len(state["messages"]),
                "context": recent,
                "source": "chat_inbox",
            }
        )

    cursor = messages[0]["message_id"] if messages else ""
    return {
        "messages": messages,
        "count": len(messages),
        "conversation_count": len(conversations),
        "skipped": skipped,
        "cursor": cursor,
        "last_seen_time": observed_at if conversations else None,
        "partial": False,
        "source": "chat_inbox_dom",
    }


def _wait_for_chat(
    page,
    *,
    user_id: str,
    expected_nickname: str = "",
    timeout: float = 5.0,
) -> dict:
    deadline = time.monotonic() + timeout
    state = _read_chat(page)
    while time.monotonic() < deadline:
        correct_route = state["url"].rstrip("/").endswith(f"/{user_id}")
        correct_nickname = (
            not expected_nickname
            or not state["target_nickname"]
            or state["target_nickname"] == expected_nickname
        )
        if state["available"] and correct_route and correct_nickname:
            return state
        time.sleep(0.2)
        state = _read_chat(page)
    return state


def _open_inbox_conversation(page, user_id: str) -> bool:
    expected = json.dumps(user_id)
    result = page.evaluate(
        f"""
        (() => {{
            const item = Array.from(document.querySelectorAll('.xhs-im-conv-item'))
                .find((node) => node.getAttribute('data-conv-id') === {expected});
            if (!item) return false;
            item.scrollIntoView({{block: 'nearest'}});
            item.click();
            return true;
        }})()
        """
    )
    return result is True
