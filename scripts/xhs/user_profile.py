"""用户主页，对应 Go xiaohongshu/user_profile.go。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .types import Feed, UserBasicInfo, UserInteraction, UserProfileResponse
from .urls import make_user_profile_url

logger = logging.getLogger(__name__)

# 提取用户数据的 JS
_EXTRACT_USER_DATA_JS = """
(() => {
    if (window.__INITIAL_STATE__ &&
        window.__INITIAL_STATE__.user &&
        window.__INITIAL_STATE__.user.userPageData) {
        const userPageData = window.__INITIAL_STATE__.user.userPageData;
        const data = userPageData.value ?? userPageData._value ?? userPageData;
        if (data) {
            return JSON.stringify(data);
        }
    }
    return "";
})()
"""

_EXTRACT_USER_NOTES_JS = """
(() => {
    if (window.__INITIAL_STATE__ &&
        window.__INITIAL_STATE__.user &&
        window.__INITIAL_STATE__.user.notes) {
        const notes = window.__INITIAL_STATE__.user.notes;
        const data = notes.value ?? notes._value ?? notes;
        if (data) {
            return JSON.stringify(data);
        }
    }
    return "";
})()
"""


def get_user_profile(page: Page, user_id: str, xsec_token: str) -> UserProfileResponse:
    """获取用户主页信息及帖子。

    Args:
        page: CDP 页面对象。
        user_id: 用户 ID。
        xsec_token: xsec_token。

    Raises:
        RuntimeError: 数据提取失败。
    """
    url = make_user_profile_url(user_id, xsec_token)
    page.navigate(url)
    page.wait_for_load()
    page.wait_dom_stable()

    return _extract_user_profile_data(page)


def _extract_user_profile_data(page: Page) -> UserProfileResponse:
    """从页面提取用户资料数据。"""
    # 等待 __INITIAL_STATE__
    _wait_for_initial_state(page)

    # 提取用户信息
    user_data_result = page.evaluate(_EXTRACT_USER_DATA_JS)
    if not user_data_result:
        raise RuntimeError("user.userPageData.value not found in __INITIAL_STATE__")

    # 提取用户帖子
    notes_result = page.evaluate(_EXTRACT_USER_NOTES_JS)
    if not notes_result:
        raise RuntimeError("user.notes.value not found in __INITIAL_STATE__")

    # 解析用户信息
    user_page_data = json.loads(user_data_result)
    basic_info = UserBasicInfo.from_dict(user_page_data.get("basicInfo", {}))
    interactions = [UserInteraction.from_dict(i) for i in user_page_data.get("interactions", [])]

    # 解析帖子（双重数组，展平）
    notes_feeds_raw = json.loads(notes_result)
    feeds: list[Feed] = []
    for feed_group in notes_feeds_raw:
        if isinstance(feed_group, list):
            for f in feed_group:
                feeds.append(Feed.from_dict(f))
        elif isinstance(feed_group, dict):
            feeds.append(Feed.from_dict(feed_group))

    return UserProfileResponse(
        user_basic_info=basic_info,
        interactions=interactions,
        feeds=feeds,
    )


def _wait_for_initial_state(page: Page, timeout: float = 10.0) -> None:
    """等待 __INITIAL_STATE__ 就绪。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = page.evaluate("window.__INITIAL_STATE__ !== undefined")
        if ready:
            return
        time.sleep(0.5)
    logger.warning("等待 __INITIAL_STATE__ 超时")


def collect_profile_history(
    page: Page, profile: UserProfileResponse, *, max_notes: int = 200
) -> tuple[UserProfileResponse, str]:
    """Collect loaded history up to the requested limit and report page coverage."""
    limit = max(1, min(int(max_notes), 1000))
    feeds = {feed.id: feed for feed in profile.feeds if feed.id}
    unchanged = 0
    complete = False
    for _ in range(50):
        complete = bool(
            page.evaluate(
                "Boolean([...document.querySelectorAll('.end-container, .no-more, .end-text')]"
                ".some(el => /没有更多|到底了|no more/i.test(el.innerText || '')))"
            )
        )
        if complete or len(feeds) >= limit or unchanged >= 2:
            break
        page.evaluate("window.scrollTo(0, document.documentElement.scrollHeight)")
        page.wait_dom_stable()
        time.sleep(0.4)
        newer = _extract_user_profile_data(page)
        before = len(feeds)
        feeds.update({feed.id: feed for feed in newer.feeds if feed.id})
        unchanged = unchanged + 1 if len(feeds) == before else 0
    profile.feeds = list(feeds.values())[:limit]
    return profile, "profile_complete" if complete and len(feeds) <= limit else "profile_sample"
