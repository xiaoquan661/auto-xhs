"""Collect latest incoming text messages from the Xiaohongshu web inbox."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from typing import Any

from collection_coverage import collection_window
from metric_temporality import display_date, event_day

from .direct_message import CHAT_URL, _read_chat
from .errors import XHSError


class PrivateHistoryOwnerMismatchError(XHSError):
    """私信客户端数据与槽位绑定账号不一致。"""

    code = "PRIVATE_HISTORY_OWNER_MISMATCH"
    result_unknown = False


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
        read_status: node.querySelector('.xhs-im-conv-item__unread-count, .unread-count')
            ? 'UNREAD' : 'UNKNOWN',
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
    page: Any,
    *,
    max_conversations: int = 20,
    context_limit: int = 10,
    expected_owner_uid: str = "",
) -> dict:
    """Read latest peer-authored text from visible one-to-one conversations."""

    max_conversations = max(1, min(int(max_conversations), 50))
    context_limit = max(1, min(int(context_limit), 20))
    page.navigate(CHAT_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    conversations, list_complete = read_conversation_history(page, max_conversations)
    message_events: list[dict] = []
    incomplete_conversations: list[dict] = []
    messages: list[dict] = []
    conversation_snapshots: list[dict] = []
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
        history = read_message_history(page, state)
        all_messages = list(history["messages"])
        events, missing = message_observations(all_messages, user_id, observed_at)
        message_events.extend(events)
        if missing or not history.get("history_complete"):
            incomplete_conversations.append(
                {
                    "user_id": user_id,
                    "undated_or_unidentified": missing,
                    "history_complete": history.get("history_complete", False),
                }
            )
        nickname = str(state.get("target_nickname") or conversation.get("nickname") or "").strip()
        conversation_snapshots.append(
            {
                "user_id": user_id,
                "nickname": nickname,
                "display_time": str(conversation.get("display_time") or ""),
                "observed_at": observed_at,
                "message_count": len(state["messages"]),
                "thread_url": state.get("url") or "",
                "read_status": str(conversation.get("read_status") or "UNKNOWN"),
                "read_status_observed_at": observed_at,
                "context": recent,
                "latest_role": str((recent[-1] if recent else {}).get("role") or ""),
            }
        )
        if not recent or recent[-1]["role"] != "peer":
            skipped.append({"user_id": user_id, "reason": "latest_message_not_peer"})
            continue
        peer_turn: list[str] = []
        for item in reversed(recent):
            if item.get("role") != "peer":
                break
            text = str(item.get("content") or "").strip()
            if text:
                peer_turn.append(text)
        peer_turn.reverse()
        if any(content in _PLATFORM_NOTICES for content in peer_turn):
            skipped.append({"user_id": user_id, "reason": "latest_message_platform_notice"})
            continue
        if not peer_turn or any(
            content.startswith("[") and content.endswith("]") for content in peer_turn
        ):
            skipped.append({"user_id": user_id, "reason": "latest_message_not_text"})
            continue
        content = "\n".join(peer_turn)
        messages.append(
            {
                "message_id": f"{user_id}:{len(state['messages'])}:{content}",
                "user_id": user_id,
                "nickname": nickname,
                "content": content,
                "display_time": str(conversation.get("display_time") or ""),
                "occurred_at": observed_at,
                "message_count": len(state["messages"]),
                "thread_url": state.get("url") or "",
                "read_status": str(conversation.get("read_status") or "UNKNOWN"),
                "read_status_observed_at": observed_at,
                "context": recent,
                "peer_turn": peer_turn,
                "source": "chat_inbox",
            }
        )

    from .private_history import collect_message_history

    try:
        native_history = collect_message_history(page, max_conversations)
    except Exception as exc:
        native_history = {"available": False, "reason": str(exc)}
    if not isinstance(native_history, dict):
        native_history = {"available": False, "reason": "私信客户端未返回有效结果"}
    owner_uid = str(native_history.get("owner_uid") or "")
    if expected_owner_uid and owner_uid and owner_uid != expected_owner_uid:
        raise PrivateHistoryOwnerMismatchError(
            f"私信数据所属账号 {owner_uid} 与槽位绑定账号 {expected_owner_uid} 不一致；"
            "本批数据未入库，请核对当前消息页的账号环境"
        )
    native_events = []
    native_missing = 0
    if isinstance(native_history, dict) and native_history.get("available"):
        for user_id in native_history["conversations"]:
            rows = [r for r in native_history["messages"] if r["user_id"] == user_id]
            events, missing = message_observations(rows, user_id, observed_at)
            native_events.extend(events)
            native_missing += missing
        message_events = native_events
        messages, conversation_snapshots, skipped = _native_reply_candidates(
            native_history,
            events=native_events,
            observed_at=observed_at,
            context_limit=context_limit,
            known_snapshots=conversation_snapshots,
        )

    cursor = messages[0]["message_id"] if messages else ""
    start, end = collection_window(observed_at)
    undated = sum(item["undated_or_unidentified"] for item in incomplete_conversations)
    complete = (
        list_complete
        and not incomplete_conversations
        and not any(row["reason"] == "conversation_unavailable" for row in skipped)
    )
    coverage_reason = "部分消息缺少独立标识或发生日期" if undated else "会话历史尚未完整采集"
    page_unknown = not conversations and not list_complete and not native_history.get("available")
    if page_unknown:
        coverage_reason = "未识别到会话或明确空态，无法确认私信数量"
    if isinstance(native_history, dict) and native_history.get("available"):
        complete = native_history["complete"] and native_missing == 0
        undated = native_missing
        incomplete_conversations = []
        coverage_reason = native_history.get("reason") or (
            "部分消息缺少独立标识或发生日期" if native_missing else ""
        )
    return {
        "messages": messages,
        "conversation_snapshots": conversation_snapshots,
        "count": None if page_unknown else len(messages),
        "conversation_count": (
            None
            if page_unknown
            else len(conversation_snapshots)
            if native_history.get("available")
            else len(conversations)
        ),
        "status": "SUCCESS" if complete else "UNKNOWN" if page_unknown else "PARTIAL",
        "reason": coverage_reason if not complete else "",
        "skipped": skipped,
        "cursor": cursor,
        "last_seen_time": observed_at if conversation_snapshots or conversations else None,
        "partial": not complete,
        "source": "chat_message_history" if native_history.get("available") else "chat_inbox_dom",
        "message_events": message_events,
        "collection_batches": [
            {
                "metric": "received_private_messages",
                "unit": "message",
                "period_start": start,
                "period_end": end,
                "coverage": "complete" if complete else "unknown" if page_unknown else "partial",
                "undated_count": undated,
                "observed_count": (
                    None
                    if page_unknown and not native_history.get("available")
                    else len(message_events)
                ),
                "source": "chat_message_history",
                "event_index": [
                    {"id": row["platform_event_id"], "date": event_day(row)}
                    for row in message_events
                ],
                "history_pages": native_history.get("batches", []),
                "history_source_reason": native_history.get("reason", ""),
                "owner_uid": native_history.get("owner_uid", ""),
                "incomplete_conversations": incomplete_conversations,
                "reason": "" if complete else coverage_reason,
            }
        ],
    }


def _native_reply_candidates(
    history: dict,
    *,
    events: list[dict],
    observed_at: str,
    context_limit: int,
    known_snapshots: list[dict],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build one current peer turn per conversation from observed IM history."""
    metadata = {row["user_id"]: row for row in known_snapshots}
    incoming = {row["platform_event_id"]: row for row in events}
    candidates: list[dict] = []
    snapshots: list[dict] = []
    skipped: list[dict] = []
    for user_id in history["conversations"]:
        rows = sorted(
            [row for row in history["messages"] if row["user_id"] == user_id],
            key=lambda row: int(row.get("store_id") or 0),
        )
        recent = rows[-context_limit:]
        snapshot = {
            **metadata.get(user_id, {}),
            "user_id": user_id,
            "observed_at": observed_at,
            "message_count": len(rows),
            "thread_url": (metadata.get(user_id) or {}).get("thread_url")
            or f"{CHAT_URL}/{user_id}",
            "context": recent,
            "latest_role": str((recent[-1] if recent else {}).get("role") or ""),
        }
        snapshots.append(snapshot)
        if not recent or recent[-1].get("role") != "peer":
            skipped.append({"user_id": user_id, "reason": "latest_message_not_peer"})
            continue
        turn = []
        for row in reversed(recent):
            if row.get("role") != "peer":
                break
            turn.append(str(row.get("content") or "").strip())
        turn.reverse()
        if any(content in _PLATFORM_NOTICES for content in turn):
            skipped.append({"user_id": user_id, "reason": "latest_message_platform_notice"})
            continue
        if not turn or any(
            not text or (text.startswith("[") and text.endswith("]")) for text in turn
        ):
            skipped.append({"user_id": user_id, "reason": "latest_message_not_text"})
            continue
        identifier = f"{user_id}:{recent[-1].get('platform_message_id') or ''}"
        latest = incoming.get(identifier)
        if latest is None:
            skipped.append({"user_id": user_id, "reason": "latest_message_not_identified"})
            continue
        candidates.append(
            {
                **snapshot,
                "message_id": identifier,
                "platform_message_id": recent[-1]["platform_message_id"],
                "content": "\n".join(turn),
                "peer_turn": turn,
                "occurred_at": latest["occurred_at"],
                "event_occurred_at": latest["occurred_at"],
                "time_scope": "platform",
                "source": "chat_message_history",
            }
        )
    return candidates, snapshots, skipped


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


def message_observations(
    messages: list[dict], user_id: str, captured_at: str
) -> tuple[list[dict], int]:
    """Keep individual incoming message facts separate from reply turns."""
    events = {}
    missing = 0
    for item in messages:
        if item.get("role") != "peer" or item.get("content") in _PLATFORM_NOTICES:
            continue
        identifier = str(item.get("platform_message_id") or "")
        value = str(item.get("occurred_at") or "")
        if value.isdigit():
            seconds = int(value)
            value = datetime.fromtimestamp(
                seconds / 1000 if seconds > 10_000_000_000 else seconds, UTC
            ).isoformat()
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            value = display_date(str(item.get("display_time") or ""), captured_at)
        if not identifier or not value:
            missing += 1
            continue
        key = f"{user_id}:{identifier}"
        events[key] = {
            "event_type": "private_message_item",
            "platform_event_id": key,
            "occurred_at": value,
            "payload": {
                **item,
                "user_id": user_id,
                "event_occurred_at": value,
                "captured_at": captured_at,
                "time_scope": "platform",
                "source": "chat_message_history",
                "metric_semantics": "message",
                "unit": "message",
            },
        }
    return list(events.values()), missing


def read_message_history(page, state: dict, *, max_pages: int = 20) -> dict:
    """Load earlier chat messages within the current conversation."""
    messages = list(state["messages"])
    unchanged = 0
    for _ in range(max_pages):
        if state.get("history_complete"):
            break
        moved = page.evaluate("""(() => {
          const first = document.querySelector('.chat-item');
          let node = first?.parentElement;
          while (node && node !== document.body) {
            if (node.scrollHeight > node.clientHeight &&
                /auto|scroll/.test(getComputedStyle(node).overflowY)) {
              node.scrollTop = 0; return true;
            }
            node = node.parentElement;
          }
          return false;
        })()""")
        if moved is not True:
            break
        page.wait_dom_stable()
        newer = _read_chat(page)
        if not newer["available"] or newer["url"] != state["url"]:
            break
        current = newer["messages"]
        unchanged = unchanged + 1 if current == state["messages"] else 0
        # Preserve identified messages if the page virtualizes older DOM nodes.
        identified = {
            r["platform_message_id"]: r for r in current + messages if r.get("platform_message_id")
        }
        messages = (
            list(identified.values())
            if identified and all(r.get("platform_message_id") for r in current + messages)
            else current
        )
        state = newer
        if unchanged >= 2:
            break
    return {**state, "messages": messages}


def read_conversation_history(page, limit: int) -> tuple[list[dict], bool]:
    """Enumerate the conversation list, retaining a bounded continuation state."""
    found: dict[str, dict] = {}
    unchanged = 0
    for _ in range(20):
        values = page.evaluate(_READ_CONVERSATIONS_JS)
        before = len(found)
        for item in values if isinstance(values, list) else []:
            found[item["user_id"]] = item
        # A limit always means partial coverage, even if a visible end marker exists.
        if len(found) >= limit:
            return list(found.values())[:limit], False
        complete = (
            page.evaluate(r"""(() => {
          if (!/^\/chat(?:\/|$)/.test(location.pathname)) return false;
          const item = document.querySelector('.xhs-im-conv-item');
          const list = item?.parentElement || document.querySelector('.xhs-im-conv-list');
          const markers = list?.querySelectorAll('.no-more, .end-text, .empty-text') || [];
          return [...markers].some(el => el.getClientRects().length &&
            /没有更多|暂无会话|no more/i.test(el.innerText || ''));
        })()""")
            is True
        )
        if complete:
            return list(found.values()), True
        unchanged = unchanged + 1 if len(found) == before else 0
        if unchanged >= 2:
            break
        moved = page.evaluate(r"""(() => {
          let node = document.querySelector('.xhs-im-conv-item')?.parentElement;
          while (node && node !== document.body) {
            if (node.scrollHeight > node.clientHeight &&
                /auto|scroll/.test(getComputedStyle(node).overflowY)) {
              node.scrollTop = node.scrollHeight; return true;
            }
            node = node.parentElement;
          }
          return false;
        })()""")
        if moved is not True:
            break
        page.wait_dom_stable()
    return list(found.values()), False
