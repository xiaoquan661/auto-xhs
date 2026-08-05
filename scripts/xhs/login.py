"""登录管理，对应 Go xiaohongshu/login.go。"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time

_QR_DIR = os.path.join(tempfile.gettempdir(), "xhs")
_QR_FILE = os.path.join(_QR_DIR, "login_qrcode.png")

from .cdp import Page
from .errors import RateLimitError
from .human import sleep_random
from .selectors import (
    AGREE_CHECKBOX,
    AGREE_CHECKBOX_CHECKED,
    CODE_INPUT,
    GET_CODE_BUTTON,
    LOGIN_CONTAINER,
    LOGIN_ERR_MSG,
    LOGIN_STATUS,
    LOGOUT_MENU_ITEM,
    LOGOUT_MORE_BUTTON,
    PHONE_INPUT,
    PHONE_LOGIN_SUBMIT,
    QRCODE_IMG,
    USER_NICKNAME,
    USER_PROFILE_NAV_LINK,
)
from .urls import EXPLORE_URL

logger = logging.getLogger(__name__)

_EMPTY_IDENTITY = {
    "logged_in": True,
    "user_id": "",
    "nickname": "",
    "profile_url": "",
}


def _profile_user_id(url: str) -> str:
    """Extract the UID from an XHS profile URL without trusting other page links."""
    match = re.search(r"/user/profile/([^/?#]+)", url)
    return match.group(1) if match else ""


def _is_current_user_profile(page: Page, current_url: str) -> bool:
    """Return whether the open profile page exposes the owner-only edit action."""
    if not _profile_user_id(current_url):
        return False
    return page.evaluate(
        """
        (() => Array.from(document.querySelectorAll(
            'button, a, [role="button"], [aria-label], [title]'
        )).some((element) => {
            const text = (element.innerText || element.textContent || '').trim();
            const aria = (element.getAttribute('aria-label') || '').trim();
            const title = (element.getAttribute('title') || '').trim();
            return text === '编辑资料' || aria === '编辑资料' || title === '编辑资料';
        }))()
        """
    ) is True


def _find_profile_nav_href(page: Page) -> str:
    """Find the signed-in user's sidebar profile link with a guarded fallback."""
    profile_href = page.evaluate(
        "document.querySelector("
        f"{json.dumps(USER_PROFILE_NAV_LINK)}"
        ")?.getAttribute('href') || ''"
    )
    if profile_href and _profile_user_id(str(profile_href)):
        return str(profile_href)

    # The exact sidebar selector changes occasionally. Only accept a profile link
    # whose own text/accessibility label explicitly identifies the "我" entry;
    # never fall back to an arbitrary author profile link.
    fallback_href = page.evaluate(
        """
        (() => {
            const links = Array.from(
                document.querySelectorAll('a[href*="/user/profile/"]')
            );
            const profileLink = links.find((link) => {
                const text = (link.innerText || link.textContent || '').trim();
                const aria = (link.getAttribute('aria-label') || '').trim();
                const title = (link.getAttribute('title') || '').trim();
                const labelledChild = link.querySelector(
                    '[aria-label="我"], [title="我"]'
                );
                return text === '我' || aria === '我' || title === '我' || labelledChild;
            });
            return profileLink?.getAttribute('href') || '';
        })()
        """
    )
    return str(fallback_href or "")


def _identity_from_profile(page: Page, profile_href: str) -> dict:
    """Navigate to a verified self-profile link and read its stable identity fields."""
    navigation_url = (
        profile_href
        if profile_href.startswith("http")
        else f"https://www.xiaohongshu.com{profile_href}"
    )
    user_id = _profile_user_id(navigation_url)
    if not user_id:
        return dict(_EMPTY_IDENTITY)

    profile_url = f"https://www.xiaohongshu.com/user/profile/{user_id}"
    current_url = str(page.evaluate("location.href") or "")
    if current_url != navigation_url:
        page.navigate(navigation_url)
        page.wait_for_load()
        page.wait_dom_stable()

    nickname = page.evaluate(
        f"document.querySelector({json.dumps(USER_NICKNAME)})?.innerText?.trim() || ''"
    )
    return {
        "logged_in": True,
        "user_id": user_id,
        "nickname": nickname or "",
        "profile_url": profile_url,
    }


def _wait_for_countdown(page: Page, timeout: float = 5.0) -> None:
    """等待"获取验证码"按钮出现倒计时数字，确认验证码已发送。

    轮询按钮文字直到包含数字（如 "60s"），超时则抛出 RateLimitError。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        btn_text = page.get_element_text(GET_CODE_BUTTON) or ""
        if any(ch.isdigit() for ch in btn_text):
            return
        time.sleep(0.3)
    raise RateLimitError()



def get_current_user_identity(page: Page) -> dict:
    """Read the currently logged-in XHS UID and nickname."""
    try:
        current_url = str(page.evaluate("location.href") or "")
        if _profile_user_id(current_url):
            try:
                page.wait_for_load()
                page.wait_dom_stable()
                current_url = str(page.evaluate("location.href") or current_url)
                if _is_current_user_profile(page, current_url):
                    return _identity_from_profile(page, current_url)
            except Exception as exc:
                logger.debug("当前主页身份探测失败，将回退到探索页: %s", exc)

        page.navigate(EXPLORE_URL)
        page.wait_for_load()
        if not check_login_status(page):
            return {
                "logged_in": False,
                "user_id": "",
                "nickname": "",
                "profile_url": "",
            }

        # 从导航栏"我"的链接取个人主页 URL（含 /user/profile/<user_id>）。
        # 精确选择器失效时，只接受文本/无障碍标签明确为"我"的链接。
        profile_href = _find_profile_nav_href(page)
        if not profile_href:
            return dict(_EMPTY_IDENTITY)

        return _identity_from_profile(page, profile_href)
    except Exception as exc:
        logger.warning("获取当前登录身份失败: %s", exc)
        return {
            "logged_in": False,
            "user_id": "",
            "nickname": "",
            "profile_url": "",
            "error": str(exc),
        }


def get_current_user_nickname(page: Page) -> str:
    """获取当前登录用户的真实昵称，失败时返回空字符串（best-effort）。"""
    return str(get_current_user_identity(page).get("nickname") or "")


def check_login_status(page: Page) -> bool:
    """检查登录状态。

    Returns:
        True 已登录，False 未登录。
    """
    # 如果当前页面已在 explore，跳过重复导航
    current_url = page.evaluate("location.href") or ""
    if "explore" not in current_url:
        page.navigate(EXPLORE_URL)
        page.wait_for_load()

    # 直接等待登录状态或登录容器出现，替代 _wait_for_auth_ui
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        if page.has_element(LOGIN_STATUS):
            return True
        if page.has_element(LOGIN_CONTAINER):
            return False
        time.sleep(0.2)
    return False


def fetch_qrcode(page: Page) -> tuple[bytes, str, bool]:
    """获取登录二维码图片。

    直接读取 img.src（data:image/png;base64,...），跳过 Canvas 绘制。

    Returns:
        (png_bytes, b64_str, already_logged_in)
        - 如果已登录，返回 (b"", "", True)
        - 如果未登录，返回 (png_bytes, b64_str, False)
    """
    # 如果当前页面已在 explore（如 check-login 刚导航过），跳过重复导航
    current_url = page.evaluate("location.href") or ""
    if "explore" not in current_url:
        page.navigate(EXPLORE_URL)
        page.wait_for_load()

    # 快速检查是否已登录，避免无谓等待二维码
    if page.has_element(LOGIN_STATUS):
        return b"", "", True

    # 直接等待二维码元素出现，合并了 _wait_for_auth_ui 的逻辑
    page.wait_for_element(QRCODE_IMG, timeout=15.0)

    # img.src 本身就是 data:image/png;base64,...，直接读取
    src = page.evaluate(
        f"document.querySelector({json.dumps(QRCODE_IMG)})?.src || ''"
    )
    if not src or "base64," not in src:
        raise RuntimeError("二维码图片 src 读取失败")

    b64_str = src.split("base64,", 1)[1]

    import base64
    png_bytes = base64.b64decode(b64_str)

    return png_bytes, b64_str, False


def _decode_qr_content(png_bytes: bytes) -> str | None:
    """通过 goqr.me read API 解码二维码内容。

    Returns:
        解码后的文本（通常是登录 URL），失败返回 None。
    """
    import http.client

    boundary = "----XhsQrBoundary"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file";'
        f' filename="qr.png"\r\n'
        f"Content-Type: image/png\r\n\r\n"
    ).encode() + png_bytes + f"\r\n--{boundary}--\r\n".encode()

    try:
        conn = http.client.HTTPSConnection(
            "api.qrserver.com", timeout=5
        )
        conn.request(
            "POST",
            "/v1/read-qr-code/",
            body=body,
            headers={
                "Content-Type": (
                    f"multipart/form-data; boundary={boundary}"
                ),
            },
        )
        resp = conn.getresponse()
        if resp.status != 200:
            return None
        result = json.loads(resp.read().decode())
        data = result[0]["symbol"][0].get("data")
        return data if data else None
    except Exception:
        logger.debug("goqr.me 解码失败，将使用 base64 fallback")
        return None


def make_qrcode_url(
    png_bytes: bytes,
) -> tuple[str, str | None]:
    """生成二维码展示 URL 和登录链接。

    通过 goqr.me read API 解码 QR 内容，构造 API 图片 URL
    （~270 字符）和小红书官方登录链接。

    Returns:
        (image_url, login_url)
        - image_url: 可用于 markdown 图片的 URL
        - login_url: 小红书官方登录链接（解码失败时为 None）
    """
    import base64
    import urllib.parse

    qr_content = _decode_qr_content(png_bytes)
    if qr_content:
        image_url = (
            "https://api.qrserver.com/v1/create-qr-code/"
            "?size=300x300&data="
            + urllib.parse.quote(qr_content, safe="")
        )
        return image_url, qr_content

    # fallback: base64 data URL
    b64 = base64.b64encode(png_bytes).decode()
    return "data:image/png;base64," + b64, None


def save_qrcode_to_file(png_bytes: bytes) -> str:
    """将二维码 PNG 字节保存到临时文件，返回文件路径。

    Args:
        png_bytes: CDP 截图返回的 PNG 字节。

    Returns:
        file_path: 保存的 PNG 文件绝对路径。
    """
    os.makedirs(_QR_DIR, exist_ok=True)
    with open(_QR_FILE, "wb") as f:
        f.write(png_bytes)
    logger.info("二维码已保存: %s", _QR_FILE)
    return _QR_FILE


def send_phone_code(page: Page, phone: str) -> bool:
    """填写手机号并发送短信验证码。

    适用于无界面服务器场景，全程通过 CDP 操作，无需扫码。

    Args:
        page: CDP 页面对象。
        phone: 手机号（不含国家码，如 13800138000）。

    Returns:
        True 验证码已发送，False 已登录（无需再登录）。

    Raises:
        RuntimeError: 找不到登录表单或手机号输入框。
    """
    # 如果当前页面已在 explore，跳过重复导航
    current_url = page.evaluate("location.href") or ""
    if "explore" not in current_url:
        page.navigate(EXPLORE_URL)
        page.wait_for_load()

    # 直接等待登录容器出现（合并了 _wait_for_auth_ui 的逻辑，避免重复等待）
    try:
        page.wait_for_element(LOGIN_CONTAINER, timeout=10.0)
    except Exception as exc:
        # 可能已登录（没有登录容器），检查登录状态
        if page.has_element(LOGIN_STATUS):
            return False
        raise RuntimeError("找不到登录表单") from exc

    if page.has_element(LOGIN_STATUS):
        return False

    sleep_random(200, 400)

    # 点击手机号输入框并逐字输入
    page.click_element(PHONE_INPUT)
    sleep_random(200, 400)
    page.type_text(phone, delay_ms=80)
    sleep_random(200, 400)

    # 先勾选用户协议，再点获取验证码
    if not page.has_element(AGREE_CHECKBOX_CHECKED):
        page.click_element(AGREE_CHECKBOX)
        sleep_random(300, 600)

    # 点击"获取验证码"
    page.click_element(GET_CODE_BUTTON)

    # 事件驱动：轮询按钮文字直到出现倒计时数字，替代固定 2-2.5s 等待
    _wait_for_countdown(page)

    logger.info("验证码已发送至 %s", phone[:3] + "****" + phone[-4:])
    return True


def submit_phone_code(page: Page, code: str) -> bool:
    """填写短信验证码并提交登录。

    Args:
        page: CDP 页面对象。
        code: 收到的短信验证码。

    Returns:
        True 登录成功，False 失败（超时或验证码错误）。
    """
    # 点击验证码输入框，先清空再用 CDP 键盘事件逐字输入（isTrusted=true，React 能识别）
    page.click_element(CODE_INPUT)
    sleep_random(100, 200)
    page.evaluate(
        f"""(() => {{
            const el = document.querySelector({json.dumps(CODE_INPUT)});
            if (el && el.value) {{
                const setter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                setter.call(el, '');
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
            }}
        }})()"""
    )
    page.type_text(code, delay_ms=0)
    sleep_random(100, 200)

    # 点击登录按钮
    page.click_element(PHONE_LOGIN_SUBMIT)
    sleep_random(500, 1000)

    # 检查是否有错误提示
    err = page.get_element_text(LOGIN_ERR_MSG)
    if err and err.strip():
        logger.warning("登录失败: %s", err.strip())
        return False

    return wait_for_login(page, timeout=30.0)


def logout(page: Page) -> bool:
    """通过页面 UI 退出登录（点击"更多"→"退出登录"）。

    Args:
        page: CDP 页面对象。

    Returns:
        True 退出成功，False 未登录或操作失败。
    """
    page.navigate(EXPLORE_URL)
    page.wait_for_load()
    sleep_random(800, 1500)

    if not page.has_element(LOGIN_STATUS):
        logger.info("当前未登录，无需退出")
        return False

    # 点击"更多"按钮展开菜单
    page.click_element(LOGOUT_MORE_BUTTON)
    sleep_random(500, 800)

    # 等待退出菜单项出现并点击
    page.wait_for_element(LOGOUT_MENU_ITEM, timeout=5.0)
    page.click_element(LOGOUT_MENU_ITEM)
    sleep_random(1000, 1500)

    logger.info("已退出登录")
    return True


def wait_for_login(page: Page, timeout: float = 120.0) -> bool:
    """等待扫码登录完成。

    Args:
        page: CDP 页面对象。
        timeout: 超时时间（秒）。

    Returns:
        True 登录成功，False 超时。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.has_element(LOGIN_STATUS):
            logger.info("登录成功")
            return True
        time.sleep(0.3)
    return False
