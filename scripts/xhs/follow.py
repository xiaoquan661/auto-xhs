"""Inspect and follow one explicit XHS user from their public profile."""

from __future__ import annotations

import json
import time

from service_errors import ServiceError

from .cdp import Page
from .urls import make_user_profile_url

FOLLOW_BUTTON = ".xhs-user-follow-area button.follow-button"
FOLLOWABLE_STATES = {"关注", "回关"}
FOLLOWING_STATES = {"已关注", "互相关注"}

_READ_PROFILE_JS = f"""
(() => {{
    const state = window.__INITIAL_STATE__ || {{}};
    const ref = state.user && state.user.userPageData;
    const pageData = ref && (ref.value !== undefined ? ref.value : ref._value);
    const basic = pageData && pageData.basicInfo ? pageData.basicInfo : {{}};
    const button = document.querySelector({json.dumps(FOLLOW_BUTTON)});
    return JSON.stringify({{
        nickname: String(basic.nickname || ""),
        red_id: String(basic.redId || ""),
        description: String(basic.desc || ""),
        button_text: button ? String(button.innerText || "").trim() : ""
    }});
}})()
"""


class FollowUnavailableError(ServiceError):
    """The follow action was not attempted because the page was not actionable."""

    result_unknown = False

    def __init__(self, message: str) -> None:
        super().__init__("FOLLOW_UNAVAILABLE", message, 409)


class FollowResultUnknownError(ServiceError):
    """The button was clicked but the final platform state could not be read back."""

    result_unknown = True

    def __init__(self) -> None:
        super().__init__(
            "FOLLOW_RESULT_UNKNOWN",
            "已点击关注，但没有回读到明确的关注状态，请先人工检查目标主页",
            409,
        )


def inspect_follow_target(page: Page, user_id: str, xsec_token: str) -> dict:
    """Read one target profile and its current relationship without changing state."""

    user_id = str(user_id or "").strip()
    xsec_token = str(xsec_token or "").strip()
    if not user_id or not xsec_token:
        raise FollowUnavailableError("关注预览必须包含用户 ID 和 XSEC Token")
    page.navigate(make_user_profile_url(user_id, xsec_token))
    page.wait_for_load()
    page.wait_dom_stable()
    return _read_current_state(page, user_id)


def follow_user(page: Page, user_id: str, xsec_token: str) -> dict:
    """Follow one user once and verify the final relationship from the same page."""

    before = inspect_follow_target(page, user_id, xsec_token)
    if before["following"]:
        return {
            "success": True,
            "message": f"已经关注 {before['nickname'] or user_id}，未重复点击",
            "changed": False,
            "target": before,
            "readback": before,
        }
    if before["button_text"] not in FOLLOWABLE_STATES:
        raise FollowUnavailableError(
            f"目标主页当前不能关注，按钮状态为 {before['button_text'] or '未找到'}"
        )

    page.click_element(FOLLOW_BUTTON)
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        current = _read_current_state(page, user_id)
        if current["following"]:
            return {
                "success": True,
                "message": f"已关注 {current['nickname'] or user_id}",
                "changed": True,
                "target": before,
                "readback": current,
            }
        time.sleep(0.3)
    raise FollowResultUnknownError()


def _read_current_state(page: Page, user_id: str) -> dict:
    raw = page.evaluate(_READ_PROFILE_JS)
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise FollowUnavailableError("无法读取目标博主主页状态") from exc
    button_text = str(data.get("button_text") or "").strip()
    return {
        "user_id": user_id,
        "nickname": str(data.get("nickname") or ""),
        "red_id": str(data.get("red_id") or ""),
        "description": str(data.get("description") or ""),
        "button_text": button_text,
        "following": button_text in FOLLOWING_STATES,
        "can_follow": button_text in FOLLOWABLE_STATES,
    }
