"""统一 CLI 入口（Extension Bridge 版本）

通过浏览器扩展 Bridge 连接用户已打开的浏览器，无需 Chrome 调试端口。
先启动 bridge_server.py，并在浏览器中安装 XHS Bridge 扩展，再运行此 CLI。

输出: JSON（ensure_ascii=False）
退出码: 0=成功, 1=未登录, 2=错误
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys

# Windows 控制台默认编码（如 cp1252）不支持中文，强制 UTF-8
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("xhs-cli")

_IDENTITY_GUARDED_COMMANDS = {
    "post-comment",
    "reply-comment",
    "like-feed",
    "favorite-feed",
    "keyword-engagement",
    "publish",
    "publish-video",
}


# ─── 输出工具 ────────────────────────────────────────────────────────────────


def _output(data: dict, exit_code: int = 0) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))
    sys.exit(exit_code)


def _copy_text_to_clipboard(text: str) -> bool:
    """Copy local setup material without putting it in normal CLI output."""
    import platform
    import subprocess

    commands = {
        "Windows": ["clip.exe"],
        "Darwin": ["pbcopy"],
        "Linux": ["xclip", "-selection", "clipboard"],
    }
    command = commands.get(platform.system())
    if not command:
        return False
    try:
        subprocess.run(
            command,
            input=text,
            text=True,
            check=True,
            capture_output=True,
        )
        return True
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False


def _ensure_switch_allows_command(args: argparse.Namespace) -> None:
    if getattr(args, "allow_during_switch", False):
        return
    from account_identity import load_switch_state

    if load_switch_state(args.account):
        raise RuntimeError(
            f"账号 {args.account!r} 正在换号，当前业务命令已暂停；"
            "请先登录新账号并运行 account-switch-complete，"
            "或运行 account-switch-cancel"
        )


def _open_file_if_display(path: str) -> None:
    """有桌面时用系统默认程序打开文件。"""
    import platform
    import subprocess

    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(path)
        elif system == "Darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        logger.debug("无法自动打开文件: %s", path)


# ─── Bridge 连接 ──────────────────────────────────────────────────────────────


class _DummyBrowser:
    """空 browser 对象，保持与旧代码的兼容性。"""

    def close(self) -> None:
        pass

    def close_page(self, page) -> None:
        pass


def _ensure_bridge_ready(
    bridge_url: str,
    account_config,
) -> dict:
    """Ensure the local Bridge is running without starting or changing Chrome."""
    from bridge_lifecycle import start_bridge

    try:
        lifecycle = start_bridge(account_config)
    except RuntimeError as exc:
        logger.warning("%s", exc)
        return {"bridge_running": False}
    return {"bridge_running": lifecycle["bridge_running"]}


def _connect(args: argparse.Namespace):
    """返回 (browser, page)，browser 为空对象，page 通过 Extension Bridge 操作浏览器。"""
    from account_manager import load_account
    from xhs.bridge import BridgePage

    account_name = getattr(args, "account", "default")
    account_config = load_account(account_name)
    bridge_url = getattr(args, "bridge_url", None) or account_config.bridge_url
    startup = _ensure_bridge_ready(bridge_url, account_config)
    if not startup["bridge_running"]:
        raise RuntimeError("BLOCKED: Bridge 启动失败，请先在 WebUI 运行诊断")
    page = BridgePage(
        bridge_url,
        account=account_name,
        account_id=account_config.account_id,
        bridge_token=account_config.bridge_token,
    )
    if not page.is_extension_connected():
        profile = account_config.chrome_profile_directory or "Default"
        raise RuntimeError(
            "BLOCKED: 热登录浏览器未连接。请手动打开账号 "
            f"{account_name!r} 对应的 Chrome Profile {profile!r}，"
            "保持小红书页面和 XHS Bridge 扩展开启后重试"
        )
    if getattr(args, "command", None) in _IDENTITY_GUARDED_COMMANDS:
        from account_identity import assert_live_identity
        from xhs.login import get_current_user_identity

        assert_live_identity(account_name, get_current_user_identity(page))
    return _DummyBrowser(), page


# _connect_saved_tab / _connect_existing 在 bridge 模式下与 _connect 等价
_connect_saved_tab = _connect
_connect_existing = _connect


# ─── 子命令实现 ───────────────────────────────────────────────────────────────


def _qrcode_fallback(browser, page, args: argparse.Namespace) -> None:
    """频率限制时刷新页面返回二维码。"""
    from xhs.login import fetch_qrcode, make_qrcode_url, save_qrcode_to_file
    from xhs.urls import EXPLORE_URL

    page.navigate(EXPLORE_URL)
    page.wait_for_load()

    png_bytes, _b64_orig, already = fetch_qrcode(page)
    if already:
        _output({"logged_in": True, "message": "已登录"})
        return

    qrcode_path = save_qrcode_to_file(png_bytes)
    image_url, login_url = make_qrcode_url(png_bytes)
    _open_file_if_display(qrcode_path)

    result: dict = {
        "logged_in": False,
        "login_method": "qrcode",
        "qrcode_path": qrcode_path,
        "qrcode_image_url": image_url,
        "message": (
            "验证码发送受限，已切换为二维码登录，请扫码。"
            "扫码后运行 wait-login 等待登录结果。"
        ),
    }
    if login_url:
        result["qr_login_url"] = login_url
    _output(result, exit_code=1)


def cmd_check_login(args: argparse.Namespace) -> None:
    """检查登录状态，未登录时自动获取二维码。"""
    from xhs.login import fetch_qrcode, make_qrcode_url, save_qrcode_to_file

    browser, page = _connect(args)
    try:
        png_bytes, _b64_orig, already = fetch_qrcode(page)
        if already:
            from account_identity import identity_status
            from xhs.login import get_current_user_identity

            identity = get_current_user_identity(page)
            _output(
                {
                    "logged_in": True,
                    "identity": identity,
                    "identity_status": identity_status(args.account, identity),
                },
                exit_code=0,
            )
            return

        qrcode_path = save_qrcode_to_file(png_bytes)
        image_url, login_url = make_qrcode_url(png_bytes)
        _open_file_if_display(qrcode_path)

        result: dict = {
            "logged_in": False,
            "login_method": "qrcode",
            "qrcode_path": qrcode_path,
            "qrcode_image_url": image_url,
            "hint": "未登录，二维码已自动生成。扫码后运行 wait-login 等待登录结果",
        }
        if login_url:
            result["qr_login_url"] = login_url
        _output(result, exit_code=1)
    finally:
        browser.close()


def cmd_login(args: argparse.Namespace) -> None:
    """登录（扫码，阻塞等待完成）。"""
    from xhs.login import fetch_qrcode, make_qrcode_url, save_qrcode_to_file, wait_for_login

    browser, page = _connect(args)
    try:
        png_bytes, _b64_orig, already = fetch_qrcode(page)
        if already:
            _output({"logged_in": True, "message": "已登录"})
            return

        qrcode_path = save_qrcode_to_file(png_bytes)
        image_url, login_url = make_qrcode_url(png_bytes)
        _open_file_if_display(qrcode_path)

        result: dict = {"qrcode_path": qrcode_path, "qrcode_image_url": image_url}
        if login_url:
            result["qr_login_url"] = login_url
        logger.info("二维码已生成，等待扫码...")

        success = wait_for_login(page, timeout=120)
        _output(
            {"logged_in": success, "message": "登录成功" if success else "等待超时"},
            exit_code=0 if success else 2,
        )
    finally:
        browser.close()


def cmd_get_qrcode(args: argparse.Namespace) -> None:
    """获取登录二维码截图并立即返回（非阻塞）。"""
    from xhs.login import fetch_qrcode, make_qrcode_url, save_qrcode_to_file

    browser, page = _connect(args)
    try:
        png_bytes, _b64_orig, already = fetch_qrcode(page)
        if already:
            browser.close_page(page)
            browser.close()
            _output({"logged_in": True, "message": "已登录"})
            return

        qrcode_path = save_qrcode_to_file(png_bytes)
        image_url, login_url = make_qrcode_url(png_bytes)
        _open_file_if_display(qrcode_path)
        browser.close()

        result: dict = {
            "qrcode_path": qrcode_path,
            "qrcode_image_url": image_url,
            "message": "二维码已生成，请扫码登录。扫码后运行 wait-login 等待登录结果。",
        }
        if login_url:
            result["qr_login_url"] = login_url
        _output(result)
    finally:
        pass


def cmd_wait_login(args: argparse.Namespace) -> None:
    """等待扫码登录完成（配合 get-qrcode 使用）。"""
    from xhs.login import wait_for_login

    browser, page = _connect_saved_tab(args)
    try:
        success = wait_for_login(page, timeout=args.timeout)
        _output(
            {
                "logged_in": success,
                "message": (
                    "登录成功"
                    if success
                    else "等待超时，请重新运行 get-qrcode 获取新二维码"
                ),
            },
            exit_code=0 if success else 2,
        )
    finally:
        browser.close()


def cmd_phone_login(args: argparse.Namespace) -> None:
    """手机号+验证码登录（交互式）。"""
    from xhs.errors import RateLimitError
    from xhs.login import send_phone_code, submit_phone_code

    browser, page = _connect(args)
    try:
        sent = send_phone_code(page, args.phone)
        if not sent:
            _output({"logged_in": True, "message": "已登录，无需重新登录"})
            return

        code = args.code
        if not code:
            code = input("请输入收到的短信验证码: ").strip()

        success = submit_phone_code(page, code)
        _output(
            {"logged_in": success, "message": "登录成功" if success else "验证码错误或超时"},
            exit_code=0 if success else 2,
        )
    except RateLimitError:
        _qrcode_fallback(browser, page, args)
    finally:
        browser.close()


def cmd_send_code(args: argparse.Namespace) -> None:
    """分步登录第一步：发送手机验证码。"""
    from xhs.errors import RateLimitError
    from xhs.login import send_phone_code

    browser, page = _connect(args)
    try:
        sent = send_phone_code(page, args.phone)
        if not sent:
            _output({"logged_in": True, "message": "已登录，无需重新登录"})
            return
        _output({
            "status": "code_sent",
            "message": (
                f"验证码已发送至 {args.phone[:3]}****{args.phone[-4:]}，"
                "请运行 verify-code --code <验证码>"
            ),
        })
    except RateLimitError:
        _qrcode_fallback(browser, page, args)
    finally:
        browser.close()


def cmd_verify_code(args: argparse.Namespace) -> None:
    """分步登录第二步：填写验证码并提交。"""
    from xhs.login import submit_phone_code

    browser, page = _connect_saved_tab(args)
    try:
        success = submit_phone_code(page, args.code)
        _output(
            {"logged_in": success, "message": "登录成功" if success else "验证码错误或超时"},
            exit_code=0 if success else 2,
        )
    finally:
        browser.close()


def cmd_delete_cookies(args: argparse.Namespace) -> None:
    """退出登录（页面 UI 点击退出）。"""
    from xhs.login import logout

    browser, page = _connect(args)
    try:
        logged_out = logout(page)
        msg = "已退出登录" if logged_out else "未登录"
        _output({"success": True, "message": msg})
    finally:
        browser.close()


def cmd_list_feeds(args: argparse.Namespace) -> None:
    """获取首页 Feed 列表。"""
    from xhs.feeds import list_feeds

    browser, page = _connect(args)
    try:
        feeds = list_feeds(page)
        _output({"feeds": [f.to_dict() for f in feeds], "count": len(feeds)})
    finally:
        browser.close()


def cmd_browse_feeds(args: argparse.Namespace) -> None:
    """自动滚动首页并按时间和数量点开笔记。"""
    from xhs.browse_like import browse_feed_cycle

    browser, page = _connect(args)
    try:
        _output(
            browse_feed_cycle(
                page,
                duration_seconds=args.duration_minutes * 60,
                count=args.count,
            )
        )
    finally:
        browser.close()


def cmd_search_feeds(args: argparse.Namespace) -> None:
    """搜索 Feeds。"""
    from xhs.search import search_feeds
    from xhs.types import FilterOption

    filter_opt = FilterOption(
        sort_by=args.sort_by or "",
        note_type=args.note_type or "",
        publish_time=args.publish_time or "",
        search_scope=args.search_scope or "",
        location=args.location or "",
    )

    browser, page = _connect(args)
    try:
        feeds = search_feeds(page, args.keyword, filter_opt)
        _output({"feeds": [f.to_dict() for f in feeds], "count": len(feeds)})
    finally:
        browser.close()


def cmd_keyword_engagement(args: argparse.Namespace) -> None:
    """按关键词随机抽取笔记并点赞或收藏。"""
    from xhs.keyword_engagement import keyword_engagement

    browser, page = _connect(args)
    try:
        result = keyword_engagement(
            page,
            keyword=args.keyword,
            action=args.action,
            count=args.count,
            candidate_pool_size=args.candidate_pool_size,
            collection_duration_seconds=args.collect_minutes * 60,
        )
        _output(result, exit_code=0 if result.get("success") else 2)
    finally:
        browser.close()


def cmd_get_feed_detail(args: argparse.Namespace) -> None:
    """获取 Feed 详情。"""
    from xhs.feed_detail import get_feed_detail
    from xhs.types import CommentLoadConfig

    config = CommentLoadConfig(
        click_more_replies=args.click_more_replies,
        max_replies_threshold=args.max_replies_threshold,
        max_comment_items=args.max_comment_items,
        scroll_speed=args.scroll_speed,
    )

    browser, page = _connect(args)
    try:
        detail = get_feed_detail(
            page,
            args.feed_id,
            args.xsec_token,
            load_all_comments=args.load_all_comments,
            config=config,
            keyword=getattr(args, "keyword", "篮球"),
        )
        _output(detail.to_dict())
    except Exception as e:
        # 附带 404 诊断事件，帮助定位根因
        diagnostics: list = []
        with contextlib.suppress(Exception):
            diagnostics = page.get_404_diagnostics() or []
        err_data: dict = {"success": False, "error": str(e)}
        if diagnostics:
            latest = diagnostics[-1]
            err_data["diagnosis"] = {
                "root_cause": latest.get("diagnosis", {}).get("root_cause"),
                "cause_category": latest.get("diagnosis", {}).get("cause_category"),
                "detail": latest.get("diagnosis", {}).get("detail"),
                "how_xhs_decides": latest.get("diagnosis", {}).get("how_xhs_decides"),
                "url": latest.get("url"),
                "final_url": latest.get("final_url"),
            }
        _output(err_data, exit_code=2)
    finally:
        browser.close()


def cmd_user_profile(args: argparse.Namespace) -> None:
    """获取用户主页。"""
    from xhs.user_profile import get_user_profile

    browser, page = _connect(args)
    try:
        profile = get_user_profile(page, args.user_id, args.xsec_token)
        _output(profile.to_dict())
    finally:
        browser.close()


def cmd_post_comment(args: argparse.Namespace) -> None:
    """发表评论。"""
    from xhs.comment import post_comment

    browser, page = _connect(args)
    try:
        post_comment(page, args.feed_id, args.xsec_token, args.content)
        _output({"success": True, "message": "评论发送成功"})
    finally:
        browser.close()


def cmd_reply_comment(args: argparse.Namespace) -> None:
    """回复评论。"""
    from xhs.comment import reply_comment

    browser, page = _connect(args)
    try:
        reply_comment(
            page,
            args.feed_id,
            args.xsec_token,
            args.content,
            comment_id=args.comment_id or "",
            user_id=args.user_id or "",
        )
        _output({"success": True, "message": "回复成功"})
    finally:
        browser.close()


def cmd_like_feed(args: argparse.Namespace) -> None:
    """点赞/取消点赞。"""
    from xhs.like_favorite import like_feed, unlike_feed

    browser, page = _connect(args)
    try:
        if args.unlike:
            result = unlike_feed(page, args.feed_id, args.xsec_token)
        else:
            result = like_feed(page, args.feed_id, args.xsec_token)
        _output(result.to_dict())
    finally:
        browser.close()


def cmd_favorite_feed(args: argparse.Namespace) -> None:
    """收藏/取消收藏。"""
    from xhs.like_favorite import favorite_feed, unfavorite_feed

    browser, page = _connect(args)
    try:
        if args.unfavorite:
            result = unfavorite_feed(page, args.feed_id, args.xsec_token)
        else:
            result = favorite_feed(page, args.feed_id, args.xsec_token)
        _output(result.to_dict())
    finally:
        browser.close()


def cmd_publish(args: argparse.Namespace) -> None:
    """发布图文内容。"""
    from image_downloader import process_images
    from xhs.publish import publish_image_content
    from xhs.types import PublishImageContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    image_paths = process_images(args.images) if args.images else []
    if not image_paths:
        _output({"success": False, "error": "没有有效的图片"}, exit_code=2)

    browser, page = _connect(args)
    try:
        publish_image_content(
            page,
            PublishImageContent(
                title=title,
                content=content,
                tags=args.tags or [],
                image_paths=image_paths,
                schedule_time=args.schedule_at,
                is_original=args.original,
                visibility=args.visibility or "",
            ),
        )
        _output({"success": True, "title": title, "images": len(image_paths), "status": "发布完成"})
    finally:
        browser.close()


def cmd_fill_publish(args: argparse.Namespace) -> None:
    """只填写图文表单，不发布。"""
    from image_downloader import process_images
    from xhs.publish import fill_publish_form
    from xhs.types import PublishImageContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    image_paths = process_images(args.images) if args.images else []
    if not image_paths:
        _output({"success": False, "error": "没有有效的图片"}, exit_code=2)

    browser, page = _connect(args)
    try:
        fill_publish_form(
            page,
            PublishImageContent(
                title=title,
                content=content,
                tags=args.tags or [],
                image_paths=image_paths,
                schedule_time=args.schedule_at,
                is_original=args.original,
                visibility=args.visibility or "",
            ),
        )
        _output(
            {
                "success": True,
                "title": title,
                "images": len(image_paths),
                "status": "表单已填写，等待确认发布",
            }
        )
    finally:
        browser.close()


def cmd_fill_publish_video(args: argparse.Namespace) -> None:
    """只填写视频表单，不发布。"""
    from xhs.publish_video import fill_publish_video_form
    from xhs.types import PublishVideoContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    browser, page = _connect(args)
    try:
        fill_publish_video_form(
            page,
            PublishVideoContent(
                title=title,
                content=content,
                tags=args.tags or [],
                video_path=args.video,
                schedule_time=args.schedule_at,
                visibility=args.visibility or "",
            ),
        )
        _output(
            {
                "success": True,
                "title": title,
                "video": args.video,
                "status": "视频表单已填写，等待确认发布",
            }
        )
    finally:
        browser.close()


def cmd_click_publish(args: argparse.Namespace) -> None:
    """点击发布按钮（在用户确认后调用）。"""
    from xhs.publish import click_publish_button

    browser, page = _connect_existing(args)
    try:
        click_publish_button(page)
        _output({"success": True, "status": "发布完成"})
    finally:
        browser.close()


def cmd_save_draft(args: argparse.Namespace) -> None:
    """保存为草稿。"""
    from xhs.publish import save_as_draft

    browser, page = _connect_existing(args)
    try:
        save_as_draft(page)
        _output({"success": True, "status": "内容已保存到草稿箱"})
    finally:
        browser.close()


def cmd_long_article(args: argparse.Namespace) -> None:
    """长文模式：填写内容 + 一键排版，返回模板列表。"""
    from xhs.publish_long_article import publish_long_article

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    browser, page = _connect(args)
    try:
        template_names = publish_long_article(
            page,
            title=title,
            content=content,
            image_paths=args.images,
        )
        _output({"success": True, "templates": template_names, "status": "长文已填写，请选择模板"})
    finally:
        browser.close()


def cmd_select_template(args: argparse.Namespace) -> None:
    """选择排版模板。"""
    from xhs.publish_long_article import select_template

    browser, page = _connect_existing(args)
    try:
        selected = select_template(page, args.name)
        if selected:
            _output({"success": True, "template": args.name, "status": "模板已选择"})
        else:
            _output({"success": False, "error": f"未找到模板: {args.name}"}, exit_code=2)
    finally:
        browser.close()


def cmd_next_step(args: argparse.Namespace) -> None:
    """点击下一步 + 填写发布页描述。"""
    from xhs.publish_long_article import click_next_and_fill_description

    with open(args.content_file, encoding="utf-8") as f:
        description = f.read().strip()

    browser, page = _connect_existing(args)
    try:
        click_next_and_fill_description(page, description)
        _output({"success": True, "status": "已进入发布页，等待确认发布"})
    finally:
        browser.close()


def cmd_diagnose_404(args: argparse.Namespace) -> None:
    """获取拦截器捕获的 404 诊断事件，打印根因分析报告。"""
    browser, page = _connect(args)
    try:
        if args.clear:
            page.clear_404_diagnostics()
            _output({"success": True, "message": "诊断记录已清空"})
            return

        events = page.get_404_diagnostics()
        if not events:
            _output(
                {
                    "success": True,
                    "events": [],
                    "message": "暂无拦截记录，请在小红书页面进行操作后重试",
                }
            )
            return

        # 控制台可读报告（写到 stderr）
        logger.info("═" * 60)
        logger.info("404 诊断报告 — 共 %d 条拦截记录", len(events))
        logger.info("═" * 60)
        for i, ev in enumerate(events, 1):
            diag = ev.get("diagnosis", {})
            logger.info(
                "[%d] %s %s → HTTP %s",
                i, ev.get("method", "?"), ev.get("url", "?")[:80], ev.get("status", "?"),
            )
            logger.info("    根因: %s", diag.get("root_cause", "未知"))
            logger.info("    详情: %s", diag.get("detail", "")[:120])
            logger.info(
                "    置信: %s | 类别: %s",
                diag.get("confidence", "?"),
                diag.get("cause_category", "?"),
            )
            logger.info(
                "    时间: %s | 页面: %s",
                ev.get("timestamp", "?"),
                ev.get("pageUrl", "?")[:60],
            )
            cookies = ev.get("cookies", {})
            req = ev.get("request", {})
            logger.info(
                "    凭证: web_session=%s a1=%s xs=%s xsec_token=%s",
                cookies.get("has_web_session"), cookies.get("has_a1"),
                req.get("has_xs"), bool(req.get("xsec_token")),
            )
            logger.info("─" * 60)

        _output({"success": True, "events": events})
    finally:
        browser.close()


def cmd_check_risk(args: argparse.Namespace) -> None:
    """分析小红书风控状态：检测自动化特征与 API 拦截情况。"""

    browser, page = _connect(args)
    try:
        probe_urls = args.probe_urls or []
        report = page.analyze_risk_control(probe_urls=probe_urls)
        if not report:
            _output({"success": False, "error": "扫描返回空结果"}, exit_code=2)
            return

        risk_level = report.get("risk_level", "unknown")
        issues = report.get("issues", [])

        # 控制台可读摘要（写到 stderr，不影响 JSON stdout）
        logger.info("风控扫描完成 | 等级: %s | 问题数: %d", risk_level.upper(), len(issues))
        for issue in issues:
            logger.info("  [%s] %s", issue.get("level", "?").upper(), issue.get("msg", ""))

        _output({"success": True, "report": report})
    finally:
        browser.close()


def cmd_get_netlog(args: argparse.Namespace) -> None:
    """获取 NetLog 原始 entries（最多 500 条）。"""
    browser, page = _connect(args)
    try:
        if not page.get_netlog_enabled():
            print(json.dumps({
                "error": "netlogger 未启用",
                "hint": "请打开扩展 popup，标题 XHS Bridge 连点 5 次激活 NetLog 后重试",
            }, ensure_ascii=False, indent=2))
            sys.exit(2)

        entries = page.get_netlog()
        if args.limit:
            entries = entries[-args.limit:]
        print(json.dumps({
            "total": len(entries),
            "entries": entries,
        }, ensure_ascii=False, indent=2))
    finally:
        browser.close()


def cmd_risk_report(args: argparse.Namespace) -> None:
    """基于 NetLog 数据生成风控分析报告。"""
    from xhs.risk_analyzer import analyze

    browser, page = _connect(args)
    try:
        if not page.get_netlog_enabled():
            print(json.dumps({
                "error": "netlogger 未启用",
                "hint": "请打开扩展 popup，标题 XHS Bridge 连点 5 次激活 NetLog 后重试",
            }, ensure_ascii=False, indent=2))
            sys.exit(2)

        entries = page.get_netlog()
        report = analyze(entries)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        browser.close()


def cmd_publish_video(args: argparse.Namespace) -> None:
    """发布视频内容。"""
    from xhs.publish_video import publish_video_content
    from xhs.types import PublishVideoContent

    with open(args.title_file, encoding="utf-8") as f:
        title = f.read().strip()
    with open(args.content_file, encoding="utf-8") as f:
        content = f.read().strip()

    browser, page = _connect(args)
    try:
        publish_video_content(
            page,
            PublishVideoContent(
                title=title,
                content=content,
                tags=args.tags or [],
                video_path=args.video,
                schedule_time=args.schedule_at,
                visibility=args.visibility or "",
            ),
        )
        _output({"success": True, "title": title, "video": args.video, "status": "发布完成"})
    finally:
        browser.close()


# ─── 多账号管理 ────────────────────────────────────────────────────────────────


def cmd_account_add(args: argparse.Namespace) -> None:
    from pathlib import Path

    from account_manager import add_account, public_config

    extension_source = Path(__file__).parent.parent / "extension"
    config = add_account(
        args.name,
        bridge_port=args.port,
        extension_source=extension_source,
    )
    _output(
        {
            "success": True,
            "account": public_config(config),
            "next_step": (
                "请手动打开该槽位对应的 Chrome Profile，加载通用扩展，"
                f"再运行 python scripts/cli.py --account {config.name} "
                "account-pair-begin --confirm"
            ),
        }
    )


def cmd_account_onboard(args: argparse.Namespace) -> None:
    """Create or resume an account slot and prepare one-time extension pairing."""
    from pathlib import Path

    from account_manager import add_account, load_account, public_config
    from account_pairing import create_pairing_session

    if not args.confirm:
        raise ValueError("首次账号入驻必须显式提供 --confirm")

    created = False
    try:
        config = load_account(args.name, allow_legacy_default=False)
    except FileNotFoundError:
        extension_source = Path(__file__).parent.parent / "extension"
        config = add_account(
            args.name,
            bridge_port=args.port,
            extension_source=extension_source,
        )
        created = True

    if config.extension_instance_id:
        raise RuntimeError(
            f"账号 {config.name!r} 已完成扩展配对；请保持对应 Chrome Profile 开启"
        )

    _ensure_bridge_ready(config.bridge_url, config)
    pairing = create_pairing_session(config, ttl_seconds=args.ttl)
    pairing_bundle = pairing.pop("pairing_bundle")
    copied = _copy_text_to_clipboard(pairing_bundle)

    result = {
        "success": True,
        "created": created,
        "account": public_config(config),
        "pairing": pairing,
        "pairing_bundle_copied": copied,
        "extension_dir": config.extension_dir,
        "instruction": (
            "请手动打开目标 Chrome Profile，并在 XHS Bridge 弹窗中"
            "粘贴剪贴板内容并确认配对"
            if copied
            else "剪贴板不可用；复制输出中的 pairing_bundle 到目标扩展弹窗"
        ),
        "next_step": (
            f"python scripts/cli.py --account {config.name} account-pair-status"
        ),
    }
    if args.show_bundle or not copied:
        result["pairing_bundle"] = pairing_bundle
    _output(result)


def cmd_account_import(args: argparse.Namespace) -> None:
    from pathlib import Path

    from account_manager import import_existing_profile, public_config

    extension_source = Path(__file__).parent.parent / "extension"
    config = import_existing_profile(
        args.name,
        user_data_dir=args.user_data_dir,
        profile_directory=args.profile_directory,
        bridge_port=args.port,
        extension_source=extension_source,
        replace=args.replace,
    )
    _output(
        {
            "success": True,
            "replaced_existing_account": args.replace,
            "account": public_config(config),
            "extension_setup": {
                "required_once": True,
                "url": "chrome://extensions",
                "directory": config.extension_dir,
                "message": "请在该 Profile 中开启开发者模式并加载通用扩展目录",
            },
            "next_step": (
                "请手动打开该 Profile，加载通用扩展，"
                f"再运行 python scripts/cli.py --account {config.name} "
                "account-pair-begin --confirm"
            ),
        }
    )


def cmd_account_discover(args: argparse.Namespace) -> None:
    from pathlib import Path

    from account_manager import (
        default_chrome_user_data_dir,
        discover_chrome_profiles,
    )

    user_data_dir = args.user_data_dir or str(default_chrome_user_data_dir())
    profiles = discover_chrome_profiles(user_data_dir)
    _output(
        {
            "success": True,
            "chrome_user_data_dir": str(Path(user_data_dir).expanduser().resolve()),
            "profiles": profiles,
        }
    )


def cmd_account_list(args: argparse.Namespace) -> None:
    from application_service import ApplicationService

    _output(ApplicationService().list_accounts())


def cmd_account_start(args: argparse.Namespace) -> None:
    from account_manager import load_account, public_config
    from account_runtime import evaluate_profile_connection
    from xhs.bridge import BridgePage

    config = load_account(args.account, allow_legacy_default=False)
    bridge_url = args.bridge_url or config.bridge_url
    startup = _ensure_bridge_ready(bridge_url, config)
    if getattr(args, "bridge_only", False):
        _output(
            {
                "success": startup["bridge_running"],
                "status": "BRIDGE_READY" if startup["bridge_running"] else "BLOCKED",
                "server_running": startup["bridge_running"],
                "chrome_managed": False,
                "message": "Bridge 已启动；Chrome 由用户手动打开并保持在线",
                "account": public_config(config),
            },
            exit_code=0 if startup["bridge_running"] else 2,
        )
    page = BridgePage(bridge_url, account=config.name)
    bridge_status = page.get_server_status()
    runtime = evaluate_profile_connection(config, bridge_status)
    ready = bool(
        runtime["bridge_running"]
        and runtime["extension_connected"]
        and runtime["profile_verified"]
    )
    result = {
        "success": ready,
        "server_running": runtime["bridge_running"],
        "extension_connected": runtime["extension_connected"],
        "profile_verified": runtime["profile_verified"],
        "expected_profile_directory": runtime["expected_profile_directory"],
        "connected_profile_directory": runtime["connected_profile_directory"],
        "profile_directory_claim_matches": runtime[
            "profile_directory_claim_matches"
        ],
        "profile_verification_level": runtime["profile_verification_level"],
        "connection_identity_verified": runtime["connection_identity_verified"],
        "extension_instance_enrolled": runtime["extension_instance_enrolled"],
        "status": "READY" if ready else "BLOCKED",
        "account": public_config(config),
    }
    if runtime["extension_connected"] and not runtime["profile_verified"]:
        result["error_code"] = "PROFILE_MISMATCH"
        result["message"] = (
            "已连接扩展的 Profile 声明或已登记实例与槽位不一致，"
            "拒绝把账号标记为启动成功"
        )
    elif not startup["bridge_running"]:
        result["error_code"] = "BRIDGE_NOT_READY"
        result["message"] = "Bridge 启动失败，请在 WebUI 运行诊断"
    elif not runtime["extension_connected"] and config.extension_dir:
        result["error_code"] = "HOT_SESSION_NOT_READY"
        result["message"] = (
            "系统不会自动启动 Chrome。请手动打开目标 Profile，"
            "保持小红书页面和 XHS Bridge 扩展开启后重新检查"
        )
        result["extension_setup"] = {
            "required_once": True,
            "url": "chrome://extensions",
            "directory": config.extension_dir,
            "message": "请在目标 Profile 中开启开发者模式并加载通用扩展",
            "before_loading": (
                "如果该 Profile 已加载旧版账号专属 XHS Bridge，"
                "请先禁用或移除；加载通用扩展后在弹窗中完成账号配对"
            ),
            "pair_command": (
                f"python scripts/cli.py --account {config.name} "
                "account-pair-begin --confirm"
            ),
        }
    _output(result, exit_code=0 if ready else 2)


def cmd_account_status(args: argparse.Namespace) -> None:
    from application_service import ApplicationService

    _output(
        ApplicationService().get_account_status(
            args.account,
            bridge_url=args.bridge_url,
        )
    )


def cmd_account_sync(args: argparse.Namespace) -> None:
    from pathlib import Path

    from account_manager import load_account, public_config, sync_account_extension

    config = load_account(args.account, allow_legacy_default=False)
    source = Path(__file__).parent.parent / "extension"
    target = sync_account_extension(config, extension_source=source)
    config = load_account(args.account, allow_legacy_default=False)
    _output(
        {
            "success": True,
            "account": public_config(config),
            "extension_dir": str(target),
            "message": (
                "账号槽位已指向当前项目的通用扩展；请让所有目标 Profile 加载或重新加载该目录，"
                "然后分别执行 account-pair-begin"
            ),
        }
    )


def cmd_account_pair_begin(args: argparse.Namespace) -> None:
    from account_manager import load_account, public_config
    from account_pairing import create_pairing_session
    from xhs.bridge import BridgePage

    if not args.confirm:
        raise ValueError("生成配对码必须显式提供 --confirm")
    config = load_account(args.account, allow_legacy_default=False)
    pairing = create_pairing_session(config, ttl_seconds=args.ttl)
    bridge_url = args.bridge_url or config.bridge_url
    _ensure_bridge_ready(bridge_url, config)
    page = BridgePage(bridge_url, account=config.name)
    if not page.is_server_running():
        raise RuntimeError("Bridge 启动失败，配对会话无法使用")
    _output(
        {
            "success": True,
            "account": public_config(config),
            "pairing": pairing,
            "extension_dir": config.extension_dir,
            "instruction": "在目标 Chrome Profile 打开 XHS Bridge 弹窗并粘贴配对包",
        }
    )


def cmd_account_pair_status(args: argparse.Namespace) -> None:
    from account_manager import load_account, public_config
    from account_pairing import get_pairing_status
    from xhs.bridge import BridgePage

    config = load_account(args.account, allow_legacy_default=False)
    page = BridgePage(args.bridge_url or config.bridge_url, account=config.name)
    status = get_pairing_status(config.name)
    bridge = page.get_server_status()
    _output(
        {
            "success": True,
            "account": public_config(config),
            "pairing": status,
            "bridge_running": bridge is not None,
            "extension_connected": bool(bridge and bridge.get("extension_connected")),
            "extension": (bridge or {}).get("extension"),
        }
    )


def cmd_account_unpair(args: argparse.Namespace) -> None:
    from account_manager import load_account, public_config
    from account_pairing import revoke_account_pairing
    from xhs.bridge import BridgePage

    if not args.confirm:
        raise ValueError("解除扩展配对必须显式提供 --confirm")
    config = load_account(args.account, allow_legacy_default=False)
    page = BridgePage(
        args.bridge_url or config.bridge_url,
        account=config.name,
        account_id=config.account_id,
        bridge_token=config.bridge_token,
    )
    local_binding_cleared = False
    if page.is_extension_connected():
        local_binding_cleared = page.clear_extension_binding()
    bridge_stopped = page.shutdown_server(
        account_id=config.account_id,
        bridge_token=config.bridge_token,
    )
    updated = revoke_account_pairing(config.name)
    _output(
        {
            "success": True,
            "account": public_config(updated),
            "local_binding_cleared": local_binding_cleared,
            "bridge_stopped": bridge_stopped,
            "message": (
                "配对已撤销；如扩展当时离线，请在扩展弹窗点击“清除本地配对”"
            ),
        }
    )


def cmd_account_autostart_enable(args: argparse.Namespace) -> None:
    from account_autostart import enable_account_autostart
    from account_manager import load_account

    if not args.confirm:
        raise ValueError("启用登录自启动必须显式提供 --confirm")
    config = load_account(args.account, allow_legacy_default=False)
    _output({"success": True, "autostart": enable_account_autostart(config.name)})


def cmd_account_autostart_status(args: argparse.Namespace) -> None:
    from account_autostart import account_autostart_status
    from account_manager import load_account

    config = load_account(args.account, allow_legacy_default=False)
    _output({"success": True, "autostart": account_autostart_status(config.name)})


def cmd_account_autostart_disable(args: argparse.Namespace) -> None:
    from account_autostart import disable_account_autostart
    from account_manager import load_account

    if not args.confirm:
        raise ValueError("关闭登录自启动必须显式提供 --confirm")
    config = load_account(args.account, allow_legacy_default=False)
    _output({"success": True, "autostart": disable_account_autostart(config.name)})


def cmd_account_doctor(args: argparse.Namespace) -> None:
    from application_service import ApplicationService

    result = ApplicationService().doctor_account(args.name)
    failed = not result["healthy"] or (args.require_ready and not result["ready"])
    _output(result, exit_code=2 if failed else 0)


def cmd_account_connection_enroll(args: argparse.Namespace) -> None:
    import time

    from account_manager import enroll_extension_instance, load_account, public_config
    from xhs.bridge import BridgePage

    if not args.confirm:
        raise ValueError("登记扩展实例必须显式提供 --confirm")
    config = load_account(args.account, allow_legacy_default=False)
    if not config.account_id or not config.bridge_token:
        raise RuntimeError("账号尚未启用连接身份；请先运行 account-sync 并重新加载扩展")
    page = BridgePage(args.bridge_url or config.bridge_url, account=config.name)
    status = page.get_server_status()
    extension = (status or {}).get("extension") or {}
    instance_id = str(extension.get("instance_id") or "")
    if not instance_id:
        raise RuntimeError("没有可登记的扩展实例；通用扩展请改用 account-pair-begin")
    if extension.get("account_id") != config.account_id:
        raise RuntimeError("当前扩展连接 ID 与账号配置不匹配，拒绝登记")
    updated = enroll_extension_instance(config, instance_id)
    restarted = page.shutdown_server(
        account_id=updated.account_id,
        bridge_token=updated.bridge_token,
    )
    if restarted:
        for _ in range(30):
            if not page.is_server_running():
                break
            time.sleep(0.1)
        _ensure_bridge_ready(args.bridge_url or updated.bridge_url, updated)
    verified_status = page.get_server_status()
    verified_extension = (verified_status or {}).get("extension") or {}
    enforced = bool(
        verified_extension.get("instance_id") == instance_id
        and verified_extension.get("identity_verified")
        and verified_extension.get("instance_enrolled")
    )
    _output(
        {
            "success": enforced,
            "account": public_config(updated),
            "extension_instance_id": instance_id,
            "bridge_restarted": restarted,
            "identity_enforced": enforced,
            "message": (
                "扩展实例已登记，Bridge 已重新验证连接"
                if enforced
                else "扩展实例已登记，但尚未重新建立受认证连接"
            ),
            "next_step": (
                "运行 account-doctor --require-ready"
                if enforced
                else "旧版扩展请重新加载后运行 account-start；通用扩展请重新配对"
            ),
        },
        exit_code=0 if enforced else 2,
    )


def _observe_login_identity(args: argparse.Namespace) -> tuple[object, object, dict]:
    from xhs.login import get_current_user_identity

    browser, page = _connect(args)
    return browser, page, get_current_user_identity(page)


def cmd_account_identity(args: argparse.Namespace) -> None:
    from account_identity import (
        identity_status,
        load_switch_state,
        record_current_identity,
    )

    browser, _page, observed = _observe_login_identity(args)
    try:
        if args.record:
            if load_switch_state(args.account):
                raise RuntimeError(
                    "账号正在换号，不能用 --record 绕过核验；"
                    "请运行 account-switch-complete"
                )
            record_current_identity(
                args.account,
                observed,
                source="account-identity",
                label=args.label,
            )
        result = identity_status(args.account, observed)
        result["success"] = result["comparison"] not in {"logged_out", "mismatch"}
        _output(result, exit_code=0 if result["success"] else 2)
    finally:
        browser.close()


def cmd_account_switch_begin(args: argparse.Namespace) -> None:
    from account_identity import begin_login_switch
    from xhs.login import logout

    if not args.confirm:
        raise ValueError("开始换号会退出当前小红书账号，必须显式提供 --confirm")
    browser, page, observed = _observe_login_identity(args)
    try:
        pending = begin_login_switch(
            args.account,
            observed,
            target_user_id=args.target_user_id,
            target_label=args.label,
        )
        logged_out = logout(page)
        _output(
            {
                "success": True,
                "account": args.account,
                "switch": pending,
                "logged_out": logged_out,
                "business_tasks_blocked": True,
                "next_step": (
                    f"python scripts/cli.py --account {args.account} check-login"
                ),
            }
        )
    finally:
        browser.close()


def cmd_account_switch_complete(args: argparse.Namespace) -> None:
    from account_identity import complete_login_switch

    browser, _page, observed = _observe_login_identity(args)
    try:
        event = complete_login_switch(
            args.account,
            observed,
            expected_user_id=args.expected_user_id,
            label=args.label,
        )
        _output(
            {
                "success": True,
                "account": args.account,
                "switch": event,
                "business_tasks_blocked": False,
                "message": "新登录身份已核验并绑定，业务任务已恢复",
            }
        )
    finally:
        browser.close()


def cmd_account_switch_cancel(args: argparse.Namespace) -> None:
    from account_identity import cancel_login_switch

    if not args.confirm:
        raise ValueError("取消换号流程必须显式提供 --confirm")
    browser, _page, observed = _observe_login_identity(args)
    try:
        result = cancel_login_switch(args.account, observed, force=args.force)
        result["success"] = True
        _output(result)
    finally:
        browser.close()


def cmd_account_switch_history(args: argparse.Namespace) -> None:
    from account_identity import load_identity_history

    events = load_identity_history(args.account, limit=args.limit)
    _output({"success": True, "account": args.account, "events": events})


# ─── 参数解析 ──────────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xhs-cli",
        description="小红书自动化 CLI（Extension Bridge 版）",
    )
    parser.add_argument(
        "--bridge-url",
        default=None,
        help="覆盖账号配置中的 Bridge WebSocket 地址",
    )
    parser.add_argument(
        "--account",
        default="default",
        help="目标账号名称（不同账号可并发；默认 default）",
    )
    parser.add_argument(
        "--lock-timeout",
        type=float,
        default=30.0,
        help="等待同账号任务完成的秒数（默认 30）",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # account management
    sub = subparsers.add_parser(
        "account-onboard",
        help="一条命令创建账号槽位、启动 Bridge 并准备一次性配对",
    )
    sub.add_argument("--name", required=True, help="账号别名，如 brand-a")
    sub.add_argument("--port", type=int, help="Bridge 端口；省略时自动分配")
    sub.add_argument("--confirm", action="store_true", help="确认创建槽位并启动配对")
    sub.add_argument("--ttl", type=int, default=300, help="配对包有效秒数（30-900）")
    sub.add_argument(
        "--show-bundle",
        action="store_true",
        help="同时在 JSON 中显示配对包；默认只复制到剪贴板",
    )
    sub.set_defaults(func=cmd_account_onboard, requires_account_lock=False)

    sub = subparsers.add_parser("account-add", help="创建独立账号浏览器环境")
    sub.add_argument("--name", required=True, help="账号别名，如 brand-a")
    sub.add_argument("--port", type=int, help="Bridge 端口；省略时自动分配")
    sub.set_defaults(func=cmd_account_add, requires_account_lock=False)

    sub = subparsers.add_parser("account-import", help="绑定已有 Chrome Profile")
    sub.add_argument("--name", required=True, help="账号别名，如 brand-a")
    sub.add_argument(
        "--user-data-dir",
        required=True,
        help="Chrome User Data 根目录",
    )
    sub.add_argument(
        "--profile-directory",
        required=True,
        help="Profile 目录名，如 Default 或 Profile 2",
    )
    sub.add_argument("--port", type=int, help="Bridge 端口；省略时自动分配")
    sub.add_argument(
        "--replace",
        action="store_true",
        help="将已有账号别名重新绑定到该 Profile，并保留上一份配置备份",
    )
    sub.set_defaults(func=cmd_account_import, requires_account_lock=False)

    sub = subparsers.add_parser("account-discover", help="发现已有 Chrome Profile")
    sub.add_argument(
        "--user-data-dir",
        help="Chrome User Data 根目录；省略时使用系统默认位置",
    )
    sub.set_defaults(func=cmd_account_discover, requires_account_lock=False)

    sub = subparsers.add_parser("account-list", help="列出已配置账号")
    sub.set_defaults(func=cmd_account_list, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-start", help="启动目标账号的 Bridge 并检查热登录连接（不会启动 Chrome）"
    )
    sub.add_argument(
        "--bridge-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    sub.set_defaults(func=cmd_account_start, requires_account_lock=False)

    sub = subparsers.add_parser("account-status", help="检查目标账号运行状态")
    sub.set_defaults(func=cmd_account_status, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-sync", help="让账号槽位使用当前项目中所有 Profile 共用的通用扩展"
    )
    sub.set_defaults(func=cmd_account_sync, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-pair-begin", help="生成通用扩展的一次性配对包"
    )
    sub.add_argument("--confirm", action="store_true", help="确认创建一次性配对会话")
    sub.add_argument("--ttl", type=int, default=300, help="配对包有效秒数（30-900）")
    sub.set_defaults(func=cmd_account_pair_begin, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-pair-status", help="查看通用扩展配对状态"
    )
    sub.set_defaults(func=cmd_account_pair_status, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-unpair", help="撤销通用扩展配对并轮换连接令牌"
    )
    sub.add_argument("--confirm", action="store_true", help="确认撤销当前配对")
    sub.set_defaults(func=cmd_account_unpair, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-autostart-enable",
        help="注册 Windows 登录任务，只自动启动该账号的 Bridge",
    )
    sub.add_argument("--confirm", action="store_true", help="确认注册登录自启动任务")
    sub.set_defaults(func=cmd_account_autostart_enable, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-autostart-status", help="查看该账号的 Windows 登录自启动状态"
    )
    sub.set_defaults(func=cmd_account_autostart_status, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-autostart-disable", help="删除该账号的 Windows 登录自启动任务"
    )
    sub.add_argument("--confirm", action="store_true", help="确认删除登录自启动任务")
    sub.set_defaults(func=cmd_account_autostart_disable, requires_account_lock=False)

    sub = subparsers.add_parser("account-doctor", help="只读诊断账号配置和运行状态")
    sub.add_argument("--name", help="只检查指定账号；省略时检查全部账号")
    sub.add_argument(
        "--require-ready",
        action="store_true",
        help="Bridge 或扩展未连接时也使命令返回失败",
    )
    sub.set_defaults(func=cmd_account_doctor, requires_account_lock=False)

    sub = subparsers.add_parser(
        "account-connection-enroll",
        help="登记旧版账号专属扩展实例（迁移兼容）",
    )
    sub.add_argument("--confirm", action="store_true", help="确认登记当前连接的扩展实例")
    sub.set_defaults(func=cmd_account_connection_enroll, allow_during_switch=True)

    sub = subparsers.add_parser(
        "account-identity",
        help="读取当前登录的小红书身份并与本地记录比较",
    )
    sub.add_argument("--record", action="store_true", help="将当前 UID 记录为账号基准身份")
    sub.add_argument("--label", help="账号用途或显示备注")
    sub.set_defaults(func=cmd_account_identity, allow_during_switch=True)

    sub = subparsers.add_parser(
        "account-switch-begin",
        help="安全换号第一步：暂停业务任务并退出当前登录",
    )
    sub.add_argument("--confirm", action="store_true", help="确认退出当前小红书账号")
    sub.add_argument("--target-user-id", help="可选：预期登录的新小红书 UID")
    sub.add_argument("--label", help="新账号用途或显示备注")
    sub.set_defaults(func=cmd_account_switch_begin, allow_during_switch=True)

    sub = subparsers.add_parser(
        "account-switch-complete",
        help="安全换号最后一步：核验并绑定新登录身份",
    )
    sub.add_argument("--expected-user-id", help="可选：必须匹配的新小红书 UID")
    sub.add_argument("--label", help="新账号用途或显示备注")
    sub.set_defaults(func=cmd_account_switch_complete, allow_during_switch=True)

    sub = subparsers.add_parser(
        "account-switch-cancel",
        help="取消尚未完成的安全换号流程",
    )
    sub.add_argument("--confirm", action="store_true", help="确认取消换号流程")
    sub.add_argument(
        "--force",
        action="store_true",
        help="当前已是另一 UID 时仍取消；之后需重新记录身份",
    )
    sub.set_defaults(func=cmd_account_switch_cancel, allow_during_switch=True)

    sub = subparsers.add_parser(
        "account-switch-history",
        help="查看账号换号历史",
    )
    sub.add_argument("--limit", type=int, default=20, help="最多返回最近 N 条记录")
    sub.set_defaults(func=cmd_account_switch_history, allow_during_switch=True)

    # check-login
    sub = subparsers.add_parser("check-login", help="检查登录状态")
    sub.set_defaults(func=cmd_check_login, allow_during_switch=True)

    # login
    sub = subparsers.add_parser("login", help="登录（扫码，阻塞等待）")
    sub.set_defaults(func=cmd_login, allow_during_switch=True)

    # get-qrcode
    sub = subparsers.add_parser("get-qrcode", help="获取登录二维码截图（非阻塞）")
    sub.set_defaults(func=cmd_get_qrcode, allow_during_switch=True)

    # wait-login
    sub = subparsers.add_parser("wait-login", help="等待扫码登录完成（配合 get-qrcode）")
    sub.add_argument("--timeout", type=float, default=120.0, help="等待超时秒数 (default: 120)")
    sub.set_defaults(func=cmd_wait_login, allow_during_switch=True)

    # phone-login
    sub = subparsers.add_parser("phone-login", help="手机号+验证码登录（交互式）")
    sub.add_argument("--phone", required=True, help="手机号")
    sub.add_argument("--code", default="", help="短信验证码（省略则交互式输入）")
    sub.set_defaults(func=cmd_phone_login, allow_during_switch=True)

    # send-code
    sub = subparsers.add_parser("send-code", help="分步登录第一步：发送手机验证码")
    sub.add_argument("--phone", required=True, help="手机号")
    sub.set_defaults(func=cmd_send_code, allow_during_switch=True)

    # verify-code
    sub = subparsers.add_parser("verify-code", help="分步登录第二步：填写验证码")
    sub.add_argument("--code", required=True, help="短信验证码")
    sub.set_defaults(func=cmd_verify_code, allow_during_switch=True)

    # delete-cookies
    sub = subparsers.add_parser("delete-cookies", help="退出登录")
    sub.set_defaults(func=cmd_delete_cookies, allow_during_switch=True)

    # list-feeds
    sub = subparsers.add_parser("list-feeds", help="获取首页 Feed 列表")
    sub.set_defaults(func=cmd_list_feeds)

    # browse-feeds
    sub = subparsers.add_parser("browse-feeds", help="自动滚动首页并点开笔记")
    sub.add_argument("--duration-minutes", type=int, default=5, help="最长浏览分钟数")
    sub.add_argument("--count", type=int, default=5, help="最多点开笔记数量")
    sub.set_defaults(func=cmd_browse_feeds)

    # search-feeds
    sub = subparsers.add_parser("search-feeds", help="搜索 Feeds")
    sub.add_argument("--keyword", required=True, help="搜索关键词")
    sub.add_argument("--sort-by", help="排序: 综合|最新|最多点赞|最多评论|最多收藏")
    sub.add_argument("--note-type", help="类型: 不限|视频|图文")
    sub.add_argument("--publish-time", help="时间: 不限|一天内|一周内|半年内")
    sub.add_argument("--search-scope", help="范围: 不限|已看过|未看过|已关注")
    sub.add_argument("--location", help="位置: 不限|同城|附近")
    sub.set_defaults(func=cmd_search_feeds)

    # keyword-engagement
    sub = subparsers.add_parser(
        "keyword-engagement",
        help="按关键词筛选并随机点赞或收藏",
    )
    sub.add_argument("--keyword", required=True, help="用于筛选笔记的关键词")
    sub.add_argument(
        "--action",
        required=True,
        choices=("like", "favorite", "both"),
        help="互动方式",
    )
    sub.add_argument("--count", type=int, default=3, help="随机处理的笔记数量")
    sub.add_argument("--candidate-pool-size", type=int, default=20, help="滑动搜集的候选池数量")
    sub.add_argument("--collect-minutes", type=int, default=2, help="最长滑动搜集时间")
    sub.set_defaults(func=cmd_keyword_engagement)

    # get-feed-detail
    sub = subparsers.add_parser("get-feed-detail", help="获取 Feed 详情")
    sub.add_argument("--feed-id", required=True, help="Feed ID")
    sub.add_argument("--xsec-token", required=True, help="xsec_token")
    sub.add_argument("--load-all-comments", action="store_true", help="加载全部评论")
    sub.add_argument("--click-more-replies", action="store_true", help="展开更多回复")
    sub.add_argument("--max-replies-threshold", type=int, default=10)
    sub.add_argument("--max-comment-items", type=int, default=0)
    sub.add_argument("--scroll-speed", default="normal", help="slow|normal|fast")
    sub.add_argument("--keyword", default="篮球", help="风控重试时的搜索关键词")
    sub.set_defaults(func=cmd_get_feed_detail)

    # user-profile
    sub = subparsers.add_parser("user-profile", help="获取用户主页")
    sub.add_argument("--user-id", required=True)
    sub.add_argument("--xsec-token", required=True)
    sub.set_defaults(func=cmd_user_profile)

    # post-comment
    sub = subparsers.add_parser("post-comment", help="发表评论")
    sub.add_argument("--feed-id", required=True)
    sub.add_argument("--xsec-token", required=True)
    sub.add_argument("--content", required=True)
    sub.set_defaults(func=cmd_post_comment)

    # reply-comment
    sub = subparsers.add_parser("reply-comment", help="回复评论")
    sub.add_argument("--feed-id", required=True)
    sub.add_argument("--xsec-token", required=True)
    sub.add_argument("--content", required=True)
    sub.add_argument("--comment-id")
    sub.add_argument("--user-id")
    sub.set_defaults(func=cmd_reply_comment)

    # like-feed
    sub = subparsers.add_parser("like-feed", help="点赞")
    sub.add_argument("--feed-id", required=True)
    sub.add_argument("--xsec-token", required=True)
    sub.add_argument("--unlike", action="store_true")
    sub.set_defaults(func=cmd_like_feed)

    # favorite-feed
    sub = subparsers.add_parser("favorite-feed", help="收藏")
    sub.add_argument("--feed-id", required=True)
    sub.add_argument("--xsec-token", required=True)
    sub.add_argument("--unfavorite", action="store_true")
    sub.set_defaults(func=cmd_favorite_feed)

    # publish
    sub = subparsers.add_parser("publish", help="发布图文")
    sub.add_argument("--title-file", required=True)
    sub.add_argument("--content-file", required=True)
    sub.add_argument("--images", nargs="+", required=True)
    sub.add_argument("--tags", nargs="*")
    sub.add_argument("--schedule-at")
    sub.add_argument("--original", action="store_true")
    sub.add_argument("--visibility")
    sub.set_defaults(func=cmd_publish)

    # publish-video
    sub = subparsers.add_parser("publish-video", help="发布视频")
    sub.add_argument("--title-file", required=True)
    sub.add_argument("--content-file", required=True)
    sub.add_argument("--video", required=True)
    sub.add_argument("--tags", nargs="*")
    sub.add_argument("--schedule-at")
    sub.add_argument("--visibility")
    sub.set_defaults(func=cmd_publish_video)

    # fill-publish
    sub = subparsers.add_parser("fill-publish", help="填写图文表单（不发布）")
    sub.add_argument("--title-file", required=True)
    sub.add_argument("--content-file", required=True)
    sub.add_argument("--images", nargs="+", required=True)
    sub.add_argument("--tags", nargs="*")
    sub.add_argument("--schedule-at")
    sub.add_argument("--original", action="store_true")
    sub.add_argument("--visibility")
    sub.set_defaults(func=cmd_fill_publish)

    # fill-publish-video
    sub = subparsers.add_parser("fill-publish-video", help="填写视频表单（不发布）")
    sub.add_argument("--title-file", required=True)
    sub.add_argument("--content-file", required=True)
    sub.add_argument("--video", required=True)
    sub.add_argument("--tags", nargs="*")
    sub.add_argument("--schedule-at")
    sub.add_argument("--visibility")
    sub.set_defaults(func=cmd_fill_publish_video)

    # click-publish
    sub = subparsers.add_parser("click-publish", help="点击发布按钮")
    sub.set_defaults(func=cmd_click_publish)

    # save-draft
    sub = subparsers.add_parser("save-draft", help="保存为草稿")
    sub.set_defaults(func=cmd_save_draft)

    # long-article
    sub = subparsers.add_parser("long-article", help="长文模式：填写 + 一键排版")
    sub.add_argument("--title-file", required=True)
    sub.add_argument("--content-file", required=True)
    sub.add_argument("--images", nargs="*")
    sub.set_defaults(func=cmd_long_article)

    # select-template
    sub = subparsers.add_parser("select-template", help="选择排版模板")
    sub.add_argument("--name", required=True)
    sub.set_defaults(func=cmd_select_template)

    # next-step
    sub = subparsers.add_parser("next-step", help="点击下一步 + 填写描述")
    sub.add_argument("--content-file", required=True)
    sub.set_defaults(func=cmd_next_step)

    # diagnose-404
    sub = subparsers.add_parser("diagnose-404", help="获取拦截器捕获的 404 根因诊断报告")
    sub.add_argument("--clear", action="store_true", help="清空已有诊断记录")
    sub.set_defaults(func=cmd_diagnose_404)

    # check-risk
    sub = subparsers.add_parser("check-risk", help="分析小红书风控状态（自动化指纹 + API 探测）")
    sub.add_argument(
        "--probe-urls",
        nargs="*",
        dest="probe_urls",
        default=[],
        help="额外探测的 API URL 列表",
    )
    sub.set_defaults(func=cmd_check_risk)

    # get-netlog
    sub = subparsers.add_parser("get-netlog", help="获取 NetLog 原始 entries（需先在 popup 激活）")
    sub.add_argument("--limit", type=int, default=None, help="只取最近 N 条")
    sub.set_defaults(func=cmd_get_netlog)

    # risk-report
    sub = subparsers.add_parser("risk-report", help="基于 NetLog 生成风控分析报告")
    sub.set_defaults(func=cmd_risk_report)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    try:
        if getattr(args, "requires_account_lock", True):
            from run_lock import for_account

            lock = for_account(args.account)
            if not lock.acquire(timeout=args.lock_timeout):
                raise TimeoutError(
                    f"账号 {args.account!r} 正在执行其他任务，"
                    f"等待 {args.lock_timeout:g} 秒后仍未空闲"
                )
            try:
                _ensure_switch_allows_command(args)
                args.func(args)
            finally:
                lock.release()
        else:
            args.func(args)
    except Exception as e:
        logger.error("执行失败: %s", e, exc_info=True)
        _output({"success": False, "error": str(e)}, exit_code=2)


if __name__ == "__main__":
    main()
