"""Read and send one text private message through the XHS web chat UI."""

from __future__ import annotations

import json
import time

from service_errors import ServiceError

from .cdp import Page
from .urls import make_user_profile_url

CHAT_URL = "https://www.xiaohongshu.com/chat"
CHAT_EDITOR = '.xhs-im-input-bar-editor[contenteditable="true"]'
PROFILE_MESSAGE_ENTRY = '[data-auto-xhs-private-message-entry="1"]'
CHAT_SEND_ENTRY = '[data-auto-xhs-private-message-send="1"]'
MESSAGE_ITEM = ".chat-item"

_READ_CHAT_JS = r"""
(() => {
    const editor = document.querySelector('.xhs-im-input-bar-editor[contenteditable="true"]');
    const header = document.querySelector('.xhs-im-chat-window__header-name');
    const chatWindow = document.querySelector('.xhs-im-chat-window');
    const messages = Array.from(document.querySelectorAll('.chat-item')).map((item) => {
        const textNode = item.querySelector('.xhs-im-bubble__text');
        const content = String(textNode ? textNode.innerText || textNode.textContent || '' : '')
            .trim();
        const classText = String(item.className || '') + ' ' +
            Array.from(item.querySelectorAll('*')).map((node) => String(node.className || '')).join(' ');
        const role = /--right\b/.test(classText) ? 'self' : 'peer';
        return { role, content };
    }).filter((item) => item.content);
    return JSON.stringify({
        available: Boolean(editor),
        url: location.href,
        target_nickname: String(header ? header.innerText || header.textContent || '' : '').trim(),
        first_message_notice: String(
            chatWindow ? chatWindow.innerText || chatWindow.textContent || '' : ''
        ).includes('只能发送1条消息'),
        messages
    });
})()
"""

_MARK_PROFILE_ENTRY_JS = r"""
(() => {
    document.querySelectorAll('[data-auto-xhs-private-message-entry]').forEach((node) => {
        node.removeAttribute('data-auto-xhs-private-message-entry');
    });
    const allowed = new Set(['私信', '发消息', '聊天']);
    const followLabels = new Set(['关注', '已关注', '互相关注']);
    const candidates = Array.from(document.querySelectorAll('button,a,[role="button"]'));
    const isVisible = (node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' && style.visibility !== 'hidden';
    };
    const labelOf = (node) => String(
        node.getAttribute('aria-label') || node.getAttribute('title') ||
        node.innerText || node.textContent || ''
    ).trim();
    let entry = candidates.find((node) => {
        return isVisible(node) && allowed.has(labelOf(node));
    });

    // The current profile UI renders "发消息" as an unlabeled round icon:
    // it is the first visible action immediately to the right of the follow button.
    if (!entry) {
        const follow = candidates.find((node) => {
            return isVisible(node) && followLabels.has(labelOf(node));
        });
        if (follow) {
            const followRect = follow.getBoundingClientRect();
            const followCenterY = followRect.top + followRect.height / 2;
            entry = candidates
                .filter((node) => {
                    if (node === follow || !isVisible(node)) return false;
                    const rect = node.getBoundingClientRect();
                    const centerY = rect.top + rect.height / 2;
                    return rect.left >= followRect.right - 1 &&
                        Math.abs(centerY - followCenterY) <=
                            Math.max(followRect.height, rect.height) / 2;
                })
                .sort((left, right) => (
                    left.getBoundingClientRect().left - right.getBoundingClientRect().left
                ))[0];
        }
    }
    if (!entry) return false;
    entry.setAttribute('data-auto-xhs-private-message-entry', '1');
    return true;
})()
"""

_MARK_CHAT_SEND_ENTRY_JS = r"""
(() => {
    document.querySelectorAll('[data-auto-xhs-private-message-send]').forEach((node) => {
        node.removeAttribute('data-auto-xhs-private-message-send');
    });
    const editor = document.querySelector('.xhs-im-input-bar-editor[contenteditable="true"]');
    if (!editor) return false;
    const scope = editor.closest('.xhs-im-input-bar') || editor.parentElement?.parentElement;
    if (!scope) return false;
    const isVisible = (node) => {
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return rect.width > 0 && rect.height > 0 &&
            style.display !== 'none' && style.visibility !== 'hidden';
    };
    const candidates = Array.from(scope.querySelectorAll('button,a,[role="button"]'));
    const entry = candidates.find((node) => {
        if (!isVisible(node)) return false;
        const label = String(
            node.getAttribute('aria-label') || node.getAttribute('title') ||
            node.innerText || node.textContent || ''
        ).trim();
        return /^(发送|send)$/i.test(label) || /(^|[-_])send([-_]|$)/i.test(String(node.className || ''));
    });
    if (!entry) return false;
    entry.setAttribute('data-auto-xhs-private-message-send', '1');
    return true;
})()
"""

_FOCUS_CHAT_EDITOR_JS = r"""
(() => {
    const editor = document.querySelector('.xhs-im-input-bar-editor[contenteditable="true"]');
    if (!editor) return false;
    editor.focus();
    return document.activeElement === editor && editor.isContentEditable;
})()
"""
class PrivateMessageUnavailableError(ServiceError):
    """The conversation could not be opened and no send action was attempted."""

    result_unknown = False

    def __init__(self, message: str) -> None:
        super().__init__("PRIVATE_MESSAGE_UNAVAILABLE", message, 409)


class PrivateMessageResultUnknownError(ServiceError):
    """Enter was pressed but the new outgoing bubble could not be read back."""

    result_unknown = True

    def __init__(self) -> None:
        super().__init__(
            "PRIVATE_MESSAGE_RESULT_UNKNOWN",
            "已触发私信发送，但没有回读到新的己方消息，请先人工检查会话",
            409,
        )


def inspect_private_message_context(
    page: Page,
    user_id: str,
    xsec_token: str = "",
    *,
    limit: int = 10,
) -> dict:
    """Open one existing or first-message conversation and read recent text context."""

    limit = max(1, min(int(limit), 20))
    opened = _open_conversation(page, user_id, xsec_token)
    state = _read_chat(page)
    return {
        "user_id": user_id.strip(),
        "target_nickname": state["target_nickname"],
        "conversation_type": (
            "first_message"
            if not opened["existing"] or state["first_message_notice"]
            else "existing"
        ),
        "messages": state["messages"][-limit:],
        "message_count": len(state["messages"]),
    }


def send_private_message(
    page: Page,
    user_id: str,
    xsec_token: str,
    content: str,
    *,
    expected_nickname: str = "",
) -> dict:
    """Send one final text once and require a new outgoing-bubble readback."""

    content = str(content or "").strip()
    if not content:
        raise PrivateMessageUnavailableError("私信正文不能为空")
    opened = _open_conversation(page, user_id, xsec_token)
    before = _read_chat(page)
    conversation_type = (
        "first_message"
        if not opened["existing"] or before["first_message_notice"]
        else "existing"
    )
    if expected_nickname and before["target_nickname"]:
        if before["target_nickname"] != expected_nickname.strip():
            raise PrivateMessageUnavailableError("当前会话收件人与任务目标不一致")
    before_self = _matching_count(before["messages"], content, role="self")
    before_all = _matching_count(before["messages"], content)

    page.input_content_editable(CHAT_EDITOR, content)
    entered = str(
        page.evaluate(
            "String(document.querySelector(" + json.dumps(CHAT_EDITOR) + ")?.innerText || '')"
        )
        or ""
    ).strip()
    if entered != content:
        raise PrivateMessageUnavailableError("私信输入框没有完整写入最终文本")

    _trigger_send(page)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        current = _read_chat(page)
        self_count = _matching_count(current["messages"], content, role="self")
        all_count = _matching_count(current["messages"], content)
        if self_count > before_self or all_count > before_all:
            return {
                "success": True,
                "message": f"已向 {current['target_nickname'] or user_id} 发送私信",
                "target": {
                    "user_id": user_id.strip(),
                    "nickname": current["target_nickname"] or expected_nickname.strip(),
                    "conversation_type": conversation_type,
                },
                "readback": {
                    "user_id": user_id.strip(),
                    "nickname": current["target_nickname"] or expected_nickname.strip(),
                    "content": content,
                    "outgoing_message_present": True,
                },
            }
        time.sleep(0.35)
    raise PrivateMessageResultUnknownError()


def _trigger_send(page: Page) -> None:
    """Trigger the chat send action with a trusted click or trusted Enter key."""

    trusted_click = getattr(page, "click_element_trusted", None)
    if callable(trusted_click) and page.evaluate(_MARK_CHAT_SEND_ENTRY_JS) is True:
        trusted_click(CHAT_SEND_ENTRY)
        return

    if page.evaluate(_FOCUS_CHAT_EDITOR_JS) is not True:
        raise PrivateMessageUnavailableError("私信输入框无法保持可编辑和聚焦状态")
    page.press_key("Enter")


def _open_conversation(page: Page, user_id: str, xsec_token: str) -> dict:
    user_id = str(user_id or "").strip()
    xsec_token = str(xsec_token or "").strip()
    if not user_id:
        raise PrivateMessageUnavailableError("私信必须包含明确收件人")

    page.navigate(f"{CHAT_URL}/{user_id}")
    page.wait_for_load()
    page.wait_dom_stable()
    if _read_chat(page)["available"]:
        return {"existing": True}

    if not xsec_token:
        raise PrivateMessageUnavailableError("首次私信需要目标主页的 XSEC Token")
    page.navigate(make_user_profile_url(user_id, xsec_token))
    page.wait_for_load()
    page.wait_dom_stable()
    if page.evaluate(_MARK_PROFILE_ENTRY_JS) is not True:
        raise PrivateMessageUnavailableError("目标主页当前没有可用的私信入口")
    trusted_click = getattr(page, "click_element_trusted", None)
    if callable(trusted_click):
        trusted_click(PROFILE_MESSAGE_ENTRY)
    else:
        page.click_element(PROFILE_MESSAGE_ENTRY)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if _read_chat(page)["available"]:
            return {"existing": False}
        time.sleep(0.35)

    # Older loaded extensions keep selecting the first XHS tab. Move the original
    # profile tab out of that set so the next Bridge call attaches to the new chat.
    page.navigate("about:blank")
    page.wait_for_load()
    page.wait_dom_stable()
    if _read_chat(page)["available"]:
        return {"existing": False}

    final_state = _read_chat(page)
    raise PrivateMessageUnavailableError(
        "点击私信入口后没有打开会话输入框；当前页面："
        f"{final_state['url'] or '未知'}"
    )


def _read_chat(page: Page) -> dict:
    raw = page.evaluate(_READ_CHAT_JS)
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise PrivateMessageUnavailableError("无法读取私信会话状态") from exc
    return {
        "available": bool(data.get("available")),
        "url": str(data.get("url") or ""),
        "target_nickname": str(data.get("target_nickname") or "").strip(),
        "first_message_notice": bool(data.get("first_message_notice")),
        "messages": [
            {
                "role": "self" if item.get("role") == "self" else "peer",
                "content": str(item.get("content") or "").strip(),
            }
            for item in list(data.get("messages") or [])
            if str(item.get("content") or "").strip()
        ],
    }


def _matching_count(messages: list[dict], content: str, role: str | None = None) -> int:
    return sum(
        item.get("content") == content and (role is None or item.get("role") == role)
        for item in messages
    )
