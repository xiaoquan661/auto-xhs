from __future__ import annotations

import json

import pytest

import scripts.xhs.follow as follow_module
from scripts.xhs.follow import (
    FOLLOW_BUTTON,
    FollowResultUnknownError,
    FollowUnavailableError,
    follow_user,
    inspect_follow_target,
)


class FakePage:
    def __init__(self, states: list[dict]) -> None:
        self.states = states
        self.index = 0
        self.clicked: list[str] = []
        self.navigated = ""

    def navigate(self, url: str) -> None:
        self.navigated = url

    def wait_for_load(self) -> None:
        pass

    def wait_dom_stable(self) -> None:
        pass

    def evaluate(self, _script: str) -> str:
        state = self.states[min(self.index, len(self.states) - 1)]
        if self.clicked and self.index < len(self.states) - 1:
            self.index += 1
            state = self.states[self.index]
        return json.dumps(state, ensure_ascii=False)

    def click_element(self, selector: str) -> None:
        self.clicked.append(selector)


def _state(button_text: str) -> dict:
    return {
        "nickname": "目标博主",
        "red_id": "red-1",
        "description": "主页简介",
        "button_text": button_text,
    }


def test_inspect_follow_target_reads_profile_without_clicking() -> None:
    page = FakePage([_state("关注")])

    result = inspect_follow_target(page, "user-1", "token-1")

    assert result["nickname"] == "目标博主"
    assert result["can_follow"] is True
    assert result["following"] is False
    assert page.clicked == []
    assert "/user/profile/user-1" in page.navigated


def test_follow_user_clicks_once_and_requires_following_readback() -> None:
    page = FakePage([_state("关注"), _state("已关注")])

    result = follow_user(page, "user-1", "token-1")

    assert page.clicked == [FOLLOW_BUTTON]
    assert result["success"] is True
    assert result["changed"] is True
    assert result["readback"]["following"] is True


def test_follow_user_is_idempotent_when_already_following() -> None:
    page = FakePage([_state("互相关注")])

    result = follow_user(page, "user-1", "token-1")

    assert page.clicked == []
    assert result["changed"] is False


def test_follow_user_rejects_unrecognized_state_before_click() -> None:
    page = FakePage([_state("发消息")])

    with pytest.raises(FollowUnavailableError):
        follow_user(page, "user-1", "token-1")

    assert page.clicked == []


def test_follow_user_does_not_retry_when_readback_is_unknown(monkeypatch) -> None:
    page = FakePage([_state("关注")])
    moments = iter([0.0, 7.0])
    monkeypatch.setattr(follow_module.time, "monotonic", lambda: next(moments))

    with pytest.raises(FollowResultUnknownError):
        follow_user(page, "user-1", "token-1")

    assert page.clicked == [FOLLOW_BUTTON]
