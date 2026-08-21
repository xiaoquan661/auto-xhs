from __future__ import annotations

import json

import pytest

from scripts.xhs.notifications import (
    NotificationReplyError,
    collect_comment_notifications,
    reply_to_comment_notification,
)
from scripts.xhs.urls import NOTIFICATION_URL


class NotificationPage:
    def __init__(self, captured: dict | None = None) -> None:
        self.captured = captured
        self.navigated = ""
        self.clicked: list[str] = []
        self.typed: list[tuple[str, str]] = []

    def navigate(self, url: str) -> None:
        self.navigated = url

    def wait_for_load(self) -> None:
        return None

    def wait_dom_stable(self) -> None:
        return None

    def evaluate(self, expression: str):
        if "window.__AUTO_XHS_MENTIONS__" in expression:
            return json.dumps(self.captured, ensure_ascii=False) if self.captured else None
        if "data-auto-xhs-comment-notification-send" in expression and "actual !==" in expression:
            return {"found": True}
        if "data-auto-xhs-comment-notification-editor" in expression and "editors.find" in expression:
            return {"found": True, "tag": "DIV", "placeholder": "回复 学远"}
        if "data-auto-xhs-comment-notification" in expression and "const matches" in expression:
            return {"found": True}
        if "card-refreshed" in expression:
            return "editor-closed"
        return None

    def click_element_trusted(self, selector: str) -> None:
        self.clicked.append(selector)

    def click_element(self, selector: str) -> None:
        self.clicked.append(selector)

    def input_content_editable(self, selector: str, content: str) -> None:
        self.typed.append((selector, content))

    def input_text(self, selector: str, content: str) -> None:
        self.typed.append((selector, content))


def test_collects_comment_notifications_from_observed_mentions_response(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.notifications.sleep_random", lambda *_args: None)
    page = NotificationPage(
        {
            "payload": {
                "success": True,
                "data": {
                    "message_list": [
                        {
                            "id": "notification-1",
                            "type": "comment/item",
                            "time": 1_755_600_000,
                            "user_info": {"userid": "user-1", "nickname": "学远"},
                            "comment_info": {
                                "id": "comment-1",
                                "content": "效率提升很多感谢分享",
                            },
                            "item_info": {
                                "id": "note-1",
                                "content": "十分钟桌面重启术",
                                "xsec_token": "token-1",
                            },
                        },
                        {"id": "like-1", "type": "like/item"},
                    ]
                },
            }
        }
    )

    result = collect_comment_notifications(page, max_items=20)

    assert page.navigated == NOTIFICATION_URL
    assert result["source"] == "notification_api_observation"
    assert result["count"] == 1
    comment = result["comments"][0]
    assert comment["notification_id"] == "notification-1"
    assert comment["comment_id"] == "comment-1"
    assert comment["feed_id"] == "note-1"
    assert comment["nickname"] == "学远"
    assert comment["content"] == "效率提升很多感谢分享"
    assert comment["classification"] == "comment/item"


def test_replies_inside_the_uniquely_matched_notification_card(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.notifications.sleep_random", lambda *_args: None)
    page = NotificationPage()

    result = reply_to_comment_notification(
        page,
        "有帮到你就好～",
        notification_id="notification-1",
        comment_id="comment-1",
        nickname="学远",
        original_content="效率提升很多感谢分享",
    )

    assert result["success"] is True
    assert result["readback"] == "editor-closed"
    assert len(page.clicked) == 2
    assert page.typed == [
        ("[data-auto-xhs-comment-notification-editor]", "有帮到你就好～")
    ]


def test_notification_reply_rejects_missing_observed_target_before_send() -> None:
    with pytest.raises(NotificationReplyError) as exc_info:
        reply_to_comment_notification(NotificationPage(), "回复内容")

    assert exc_info.value.result_unknown is False
