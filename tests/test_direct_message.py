from __future__ import annotations

import json

import pytest

from scripts.xhs.direct_message import (
    CHAT_EDITOR,
    CHAT_SEND_ENTRY,
    _FOCUS_CHAT_EDITOR_JS,
    _SYNC_CHAT_EDITOR_STATE_JS,
    PROFILE_MESSAGE_ENTRY,
    _MARK_CHAT_SEND_ENTRY_JS,
    _MARK_PROFILE_ENTRY_JS,
    PrivateMessageResultUnknownError,
    PrivateMessageUnavailableError,
    inspect_private_message_context,
    send_private_message,
)


class FakePage:
    def __init__(
        self,
        *,
        existing: bool,
        messages: list[dict] | None = None,
        profile_entry: bool = True,
        append_after_send: bool = True,
        profile_click_opens_new_tab: bool = False,
        send_button: bool = False,
    ) -> None:
        self.existing = existing
        self.available = existing
        self.messages = list(messages or [])
        self.profile_entry = profile_entry
        self.append_after_send = append_after_send
        self.profile_click_opens_new_tab = profile_click_opens_new_tab
        self.send_button = send_button
        self.conversation_created = False
        self.first_message_notice = False
        self.navigations: list[str] = []
        self.clicked: list[str] = []
        self.trusted_clicked: list[str] = []
        self.entered = ""
        self.pressed: list[str] = []
        self.focus_requested = False
        self.state_synced = False

    def navigate(self, url: str) -> None:
        self.navigations.append(url)
        if "/chat/" in url:
            self.available = self.existing or self.conversation_created
        elif url == "about:blank" and self.conversation_created:
            self.available = True
        elif "/user/profile/" in url:
            self.available = False

    def wait_for_load(self) -> None:
        pass

    def wait_dom_stable(self) -> None:
        pass

    def evaluate(self, script: str):
        if "data-auto-xhs-private-message-entry" in script:
            return self.profile_entry
        if script == _MARK_CHAT_SEND_ENTRY_JS:
            return self.send_button
        if script == _FOCUS_CHAT_EDITOR_JS:
            self.focus_requested = True
            return True
        if script == _SYNC_CHAT_EDITOR_STATE_JS:
            self.state_synced = True
            return True
        if script.startswith("String(document.querySelector"):
            return self.entered
        return json.dumps(
            {
                "available": self.available,
                "url": self.navigations[-1] if self.navigations else "",
                "target_nickname": "目标博主" if self.available else "",
                "first_message_notice": self.first_message_notice,
                "messages": self.messages,
            },
            ensure_ascii=False,
        )

    def click_element(self, selector: str) -> None:
        self.clicked.append(selector)
        self.available = True

    def click_element_trusted(self, selector: str) -> None:
        self.trusted_clicked.append(selector)
        if selector == CHAT_SEND_ENTRY and self.append_after_send:
            self.messages.append({"role": "self", "content": self.entered})
            return
        if selector == PROFILE_MESSAGE_ENTRY:
            self.conversation_created = True
            self.first_message_notice = True
            self.available = not self.profile_click_opens_new_tab

    def input_content_editable(self, selector: str, content: str) -> None:
        assert selector == CHAT_EDITOR
        self.entered = content

    def press_key(self, key: str) -> None:
        self.pressed.append(key)
        if self.append_after_send:
            self.messages.append({"role": "self", "content": self.entered})


def test_reads_recent_existing_conversation_context() -> None:
    page = FakePage(
        existing=True,
        messages=[
            {"role": "peer", "content": "你好"},
            {"role": "self", "content": "你好呀"},
        ],
    )

    result = inspect_private_message_context(page, "user-1", limit=1)

    assert result["conversation_type"] == "existing"
    assert result["messages"] == [{"role": "self", "content": "你好呀"}]
    assert page.clicked == []


def test_opens_first_message_from_profile_without_sending() -> None:
    page = FakePage(existing=False)

    result = inspect_private_message_context(page, "user-1", "token-1")

    assert result["conversation_type"] == "first_message"
    assert page.clicked == []
    assert page.trusted_clicked == [PROFILE_MESSAGE_ENTRY]
    assert page.pressed == []


def test_profile_message_entry_supports_icon_next_to_follow_button() -> None:
    assert "followLabels" in _MARK_PROFILE_ENTRY_JS
    assert "rect.left >= followRect.right - 1" in _MARK_PROFILE_ENTRY_JS
    assert "getAttribute('aria-label')" in _MARK_PROFILE_ENTRY_JS


def test_reopens_first_message_when_profile_button_uses_new_tab(monkeypatch) -> None:
    page = FakePage(existing=False, profile_click_opens_new_tab=True)
    moments = iter([0.0, 9.0])
    monkeypatch.setattr(
        "scripts.xhs.direct_message.time.monotonic", lambda: next(moments)
    )

    result = inspect_private_message_context(page, "user-1", "token-1")

    assert result["conversation_type"] == "first_message"
    assert page.navigations[-1] == "about:blank"
    assert page.trusted_clicked == [PROFILE_MESSAGE_ENTRY]


def test_sends_once_and_requires_new_outgoing_readback() -> None:
    page = FakePage(existing=True)

    result = send_private_message(page, "user-1", "", "专属私信")

    assert page.pressed == ["Enter"]
    assert page.focus_requested is True
    assert page.state_synced is True
    assert result["success"] is True


def test_syncs_contenteditable_input_state_before_send() -> None:
    page = FakePage(existing=True)

    result = send_private_message(page, "user-1", "", "专属私信")

    assert page.state_synced is True
    assert page.pressed == ["Enter"]
    assert result["readback"]["outgoing_message_present"] is True
    assert result["readback"]["content"] == "专属私信"


def test_prefers_visible_send_button_when_chat_exposes_one() -> None:
    page = FakePage(existing=True, send_button=True)

    result = send_private_message(page, "user-1", "", "专属私信")

    assert page.trusted_clicked == [CHAT_SEND_ENTRY]
    assert page.pressed == []
    assert page.focus_requested is False
    assert result["success"] is True


def test_enter_fallback_keeps_editor_contenteditable() -> None:
    assert "contenteditable', 'false'" not in _FOCUS_CHAT_EDITOR_JS
    assert "editor.isContentEditable" in _FOCUS_CHAT_EDITOR_JS


def test_unavailable_first_message_fails_before_send() -> None:
    page = FakePage(existing=False, profile_entry=False)

    with pytest.raises(PrivateMessageUnavailableError):
        send_private_message(page, "user-1", "token-1", "专属私信")

    assert page.pressed == []


def test_post_send_without_readback_is_result_unknown(monkeypatch) -> None:
    page = FakePage(existing=True, append_after_send=False)
    moments = iter([0.0, 9.0])
    monkeypatch.setattr(
        "scripts.xhs.direct_message.time.monotonic", lambda: next(moments)
    )

    with pytest.raises(PrivateMessageResultUnknownError):
        send_private_message(page, "user-1", "", "专属私信")

    assert page.pressed == ["Enter"]
