"""点赞/收藏操作，对应 Go xiaohongshu/like_favorite.go。"""

from __future__ import annotations

import json
import logging
import time

from .cdp import Page
from .errors import (
    ActionResultUnknownError,
    CDPError,
    ElementNotFoundError,
    InteractionStateError,
    NoFeedDetailError,
)
from .selectors import COLLECT_BUTTON, LIKE_BUTTON_SELECTORS
from .types import ActionResult
from .urls import make_feed_detail_url

logger = logging.getLogger(__name__)

# 从 __INITIAL_STATE__ 读取互动状态的 JS
_GET_INTERACT_STATE_JS = """
(() => {
    if (window.__INITIAL_STATE__ &&
        window.__INITIAL_STATE__.note &&
        window.__INITIAL_STATE__.note.noteDetailMap) {
        return JSON.stringify(window.__INITIAL_STATE__.note.noteDetailMap);
    }
    return "";
})()
"""


def _get_interact_state(page: Page, feed_id: str) -> tuple[bool, bool]:
    """读取笔记的点赞/收藏状态。

    Returns:
        (liked, collected)

    Raises:
        NoFeedDetailError: 无法获取状态。
    """
    result = page.evaluate(_GET_INTERACT_STATE_JS)
    if not result:
        raise NoFeedDetailError()

    note_detail_map = json.loads(result)
    detail = note_detail_map.get(feed_id)
    if not detail:
        raise NoFeedDetailError()

    interact = detail.get("note", {}).get("interactInfo", {})
    if not isinstance(interact.get("liked"), bool):
        raise NoFeedDetailError()
    return interact["liked"], interact.get("collected", False)


def _prepare_page(page: Page, feed_id: str, xsec_token: str) -> None:
    """导航到 feed 详情页。"""
    url = make_feed_detail_url(feed_id, xsec_token)
    page.navigate(url)
    page.wait_for_load()
    page.wait_dom_stable()
    time.sleep(1)


# ========== 点赞 ==========


def like_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """点赞笔记（幂等：已点赞则跳过）。"""
    _prepare_page(page, feed_id, xsec_token)
    return like_current_feed(page, feed_id)


def like_current_feed(page: Page, feed_id: str) -> ActionResult:
    """Like a feed that is already open, without navigating away from the current page."""
    return _toggle_like(page, feed_id, target_liked=True)


def unlike_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """取消点赞（幂等：未点赞则跳过）。"""
    _prepare_page(page, feed_id, xsec_token)
    return _toggle_like(page, feed_id, target_liked=False)


def _find_like_button(page: Page) -> str | None:
    for attempt in range(20):
        selector = next((item for item in LIKE_BUTTON_SELECTORS if page.has_element(item)), None)
        if selector is not None:
            return selector
        if attempt < 19:
            time.sleep(0.25)
    return None


def _toggle_like(page: Page, feed_id: str, target_liked: bool) -> ActionResult:
    """执行点赞/取消点赞操作。"""
    action_name = "点赞" if target_liked else "取消点赞"

    try:
        liked, _ = _get_interact_state(page, feed_id)
    except NoFeedDetailError as exc:
        raise InteractionStateError(f"{action_name}前未读到当前笔记状态，请刷新后核对") from exc

    # 幂等检查
    if liked == target_liked:
        logger.info("feed %s 已%s，跳过", feed_id, action_name)
        return ActionResult(feed_id, True, f"已{action_name}", changed=False)

    selector = _find_like_button(page)
    if selector is None:
        raise InteractionStateError(f"{action_name}前未找到笔记操作按钮，请核对当前页面")

    try:
        page.click_element(selector)
    except ElementNotFoundError as exc:
        raise InteractionStateError(f"{action_name}前按钮已消失，请核对当前页面") from exc
    except CDPError as exc:
        if "元素不存在" in str(exc):
            raise InteractionStateError(f"{action_name}前按钮已消失，请核对当前页面") from exc
        raise ActionResultUnknownError(f"{action_name}结果待核对：{exc}") from exc

    # 单次点击后只读取平台状态，等待异步页面更新。
    try:
        for _ in range(10):
            time.sleep(0.3)
            try:
                liked, _ = _get_interact_state(page, feed_id)
            except NoFeedDetailError:
                continue
            if liked == target_liked:
                return ActionResult(feed_id, True, f"{action_name}成功", changed=True)
    except Exception as exc:
        raise ActionResultUnknownError(f"{action_name}结果待核对：{exc}") from exc
    raise ActionResultUnknownError(f"{action_name}已点击，平台状态尚未确认，请先回读核对")


# ========== 收藏 ==========


def favorite_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """收藏笔记（幂等：已收藏则跳过）。"""
    _prepare_page(page, feed_id, xsec_token)
    return _toggle_favorite(page, feed_id, target_collected=True)


def unfavorite_feed(page: Page, feed_id: str, xsec_token: str) -> ActionResult:
    """取消收藏（幂等：未收藏则跳过）。"""
    _prepare_page(page, feed_id, xsec_token)
    return _toggle_favorite(page, feed_id, target_collected=False)


def _wait_collect_button(page: Page, timeout: float = 5.0, interval: float = 0.2) -> bool:
    """等待收藏按钮出现。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.has_element(COLLECT_BUTTON):
            return True
        time.sleep(interval)
    return False


def _wait_collected_state(
    page: Page,
    feed_id: str,
    target_collected: bool,
    timeout: float = 3.0,
    interval: float = 0.3,
) -> bool:
    """短轮询验证收藏状态是否达到目标。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _, collected = _get_interact_state(page, feed_id)
            if collected == target_collected:
                return True
        except NoFeedDetailError:
            pass
        time.sleep(interval)
    return False


def _toggle_favorite(page: Page, feed_id: str, target_collected: bool) -> ActionResult:
    """执行收藏/取消收藏操作。"""
    action_name = "收藏" if target_collected else "取消收藏"

    try:
        _, collected = _get_interact_state(page, feed_id)
    except NoFeedDetailError:
        logger.warning("无法读取互动状态，直接点击")
        collected = not target_collected

    # 幂等检查
    if collected == target_collected:
        logger.info("feed %s 已%s，跳过", feed_id, action_name)
        return ActionResult(feed_id=feed_id, success=True, message=f"已{action_name}")

    if not _wait_collect_button(page, timeout=5.0):
        logger.error("feed %s 未找到收藏按钮: %s", feed_id, COLLECT_BUTTON)
        return ActionResult(
            feed_id=feed_id, success=False, message=f"{action_name}失败：未找到收藏按钮"
        )

    for attempt in range(2):
        if attempt > 0:
            logger.warning("feed %s %s首次未确认成功，重试", feed_id, action_name)

        try:
            page.click_element(COLLECT_BUTTON)
        except Exception as e:
            logger.warning("feed %s 点击收藏按钮失败（第%d次）: %s", feed_id, attempt + 1, e)
            continue

        if _wait_collected_state(page, feed_id, target_collected, timeout=3.0, interval=0.3):
            logger.info("feed %s %s成功", feed_id, action_name)
            return ActionResult(feed_id=feed_id, success=True, message=f"{action_name}成功")

    logger.error("feed %s %s未确认成功", feed_id, action_name)
    return ActionResult(feed_id=feed_id, success=False, message=f"{action_name}失败：状态未变化")
