"""图文发布，对应 Go xiaohongshu/publish.go（837 行）。"""

from __future__ import annotations

import json
import logging
import random
import re
import time

from .cdp import Page
from .errors import (
    AccountRiskControlError,
    ContentTooLongError,
    PublishError,
    TitleTooLongError,
    UploadTimeoutError,
)
from .publish_links import MANAGER_URL
from .selectors import (
    CONTENT_EDITOR,
    CONTENT_LENGTH_ERROR,
    DATETIME_INPUT,
    FILE_INPUT,
    IMAGE_PREVIEW,
    ORIGINAL_SWITCH,
    ORIGINAL_SWITCH_CARD,
    SCHEDULE_SWITCH,
    TAG_FIRST_ITEM,
    TAG_TOPIC_CONTAINER,
    TITLE_INPUT,
    TITLE_MAX_SUFFIX,
    UPLOAD_INPUT,
    VISIBILITY_DROPDOWN,
    VISIBILITY_OPTIONS,
)
from .types import PublishImageContent
from .urls import PUBLISH_URL

logger = logging.getLogger(__name__)


def fill_publish_form(page: Page, content: PublishImageContent) -> None:
    """填写图文发布表单，不点击发布按钮。

    Args:
        page: CDP 页面对象。
        content: 发布内容。

    Raises:
        PublishError: 填写失败。
        UploadTimeoutError: 上传超时。
        TitleTooLongError: 标题超长。
        ContentTooLongError: 正文超长。
    """
    if not content.image_paths:
        raise PublishError("图片不能为空")

    # 导航到发布页
    _navigate_to_publish_page(page)

    # 点击"上传图文" TAB
    _click_publish_tab(page, "上传图文")
    time.sleep(1)

    # 上传图片
    _upload_images(page, content.image_paths)

    # 标签截取
    tags = content.tags[:10] if len(content.tags) > 10 else content.tags
    if len(content.tags) > 10:
        logger.warning("标签数量超过10，截取前10个")

    logger.info(
        "发布内容: title=%s, images=%d, tags=%d, schedule=%s, original=%s, visibility=%s",
        content.title,
        len(content.image_paths),
        len(tags),
        content.schedule_time,
        content.is_original,
        content.visibility,
    )

    # 填写表单（不点击发布）
    _fill_publish_form(
        page,
        content.title,
        content.content,
        tags,
        content.schedule_time,
        content.is_original,
        content.visibility,
    )


def _select_publish_preview(page: Page, expected_title: str) -> None:
    """Locate the prepared editor before observing or clicking its controls."""
    from .errors import PublishPreviewUnavailableError

    select = getattr(page, "select_publish_tab", None)
    if callable(select):
        result = select(expected_title)
        if result.get("status") != "ready":
            raise PublishPreviewUnavailableError(
                "未找到唯一匹配的发布预览，请打开对应编辑页并重新核对预览"
            )


def click_publish_button(page: Page, *, expected_title: str = "") -> dict:
    """点击 closed-shadow 发布按钮，并用平台正向证据确认结果。

    成功只接受三类证据：发布接口成功响应、点击后出现的成功提示，或跳转到
    创作中心笔记管理页后回读到本次标题。按钮消失或页面跳转本身不算成功。
    """
    _select_publish_preview(page, expected_title)
    initial_url = str(page.evaluate("location.href") or "")

    # 1) 注入多层响应捕获。结果同时写入 sessionStorage，确保同源跳转后仍可回读。
    page.evaluate(
        """
        (() => {
            const STORAGE_KEY = '__xhs_publish_result_v1';
            try { sessionStorage.removeItem(STORAGE_KEY); } catch (e) {}
            if (window.__xhsPublishObs) window.__xhsPublishObs.disconnect();
            window.__xhsPublishResult = null;

            function looksLikePublishResp(body) {
                if (!body) return false;
                return body.includes('HTTPBizError')
                    || /"code":\\s*-913\\d/.test(body)
                    || body.includes('禁止发笔记')
                    || /"(?:note_id|noteId)":\\s*"[^"]+"/.test(body);
            }

            function capture(source, info) {
                const previous = window.__xhsPublishResult;
                if (previous && !(info.code === 0 || info.success === true)) return;
                if (previous && !JSON.stringify(info).match(/"(?:note_id|noteId)"/)) return;
                const result = {source, captured_at: Date.now(), ...info};
                window.__xhsPublishResult = result;
                try { sessionStorage.setItem(STORAGE_KEY, JSON.stringify(result)); } catch (e) {}
            }

            function captureFromBody(source, url, status, body) {
                if (!looksLikePublishResp(body)) return;
                try {
                    const j = JSON.parse(body);
                    capture(source, {url, status, ...j});
                } catch (e) {
                    // body 不是顶层 JSON，尝试 regex 抽取 code/msg
                    const codeMatch = body.match(/"code":\\s*(-?\\d+)/);
                    const msgMatch = body.match(/"msg":\\s*"([^"]+)"/);
                    capture(source, {
                        url, status,
                        code: codeMatch ? parseInt(codeMatch[1], 10) : null,
                        msg: msgMatch ? msgMatch[1] : null,
                        raw: body.slice(0, 500),
                    });
                }
            }

            // Layer 1: XHR — 内容标志判定，绕开 URL pattern 不可靠的问题
            const origOpen = XMLHttpRequest.prototype.open;
            const origSend = XMLHttpRequest.prototype.send;
            XMLHttpRequest.prototype.open = function(method, url) {
                this.__xhsPubUrl = url;
                return origOpen.apply(this, arguments);
            };
            XMLHttpRequest.prototype.send = function() {
                this.addEventListener('loadend', () => {
                    captureFromBody('xhr', this.__xhsPubUrl, this.status, this.responseText || '');
                });
                return origSend.apply(this, arguments);
            };

            // Layer 2: fetch — 同样内容判定
            const origFetch = window.fetch;
            window.fetch = async function(...args) {
                const url = typeof args[0] === 'string' ? args[0] : args[0].url;
                const resp = await origFetch.apply(this, args);
                try {
                    const txt = await resp.clone().text();
                    captureFromBody('fetch', url, resp.status, txt);
                } catch (e) {}
                return resp;
            };

            // Layer 3: console hook — XHS publish module 失败时打印 "[发布失败] HTTPBizError: ..."
            ['error', 'log', 'warn'].forEach((method) => {
                const orig = console[method];
                console[method] = function(...args) {
                    const txt = args.map((a) => {
                        if (typeof a === 'string') return a;
                        if (a && typeof a === 'object') {
                            try { return JSON.stringify(a); } catch (e) { return String(a); }
                        }
                        return String(a);
                    }).join(' ');
                    if (txt.includes('HTTPBizError') || txt.includes('发布失败')) {
                        const codeMatch = txt.match(/"code":\\s*(-?\\d+)/);
                        const msgMatch = txt.match(/"msg":\\s*"([^"]+)"/);
                        if (codeMatch) {
                            capture('console', {
                                code: parseInt(codeMatch[1], 10),
                                msg: msgMatch ? msgMatch[1] : '发布失败',
                                raw_console: txt.slice(0, 800),
                            });
                        }
                    }
                    return orig.apply(this, args);
                };
            });

            // Layer 4: DOM MutationObserver — 只采集点击后新增的明确成功或失败提示。
            const SUCCESS_KW = /笔记发布成功|发布成功|发布完成/;
            const RISK_KW = /违反|违规|禁止发笔记|审核未通过|账号异常|无法发布/;
            const obs = new MutationObserver((muts) => {
                if (window.__xhsPublishResult) return;
                muts.forEach((m) => {
                    m.addedNodes.forEach((n) => {
                        if (n.nodeType !== 1) return;
                        const txt = (n.textContent || '').trim();
                        if (txt.length === 0 || txt.length > 300) return;
                        if (SUCCESS_KW.test(txt)) {
                            capture('toast_success', {
                                success: true,
                                msg: txt,
                                cls: (n.className || '').toString().slice(0, 80),
                            });
                        } else if (RISK_KW.test(txt)) {
                            capture('toast_error', {
                                code: -9136,
                                msg: txt,
                                cls: (n.className || '').toString().slice(0, 80),
                            });
                        }
                    });
                });
            });
            obs.observe(document.body, {childList: true, subtree: true});
            window.__xhsPublishObs = obs;
        })()
        """
    )

    # Read the closed shadow button's layout and click it in one debugger session.
    click_result = page.click_publish_control()
    if (
        click_result.get("dispatched") is False
        and click_result.get("error_code") != "EXTENSION_RELOAD_REQUIRED"
    ):
        logger.warning("发布尚未派发，重新定位按钮: %s", click_result)
        time.sleep(0.5)
        click_result = page.click_publish_control()
    if not click_result.get("dispatched"):
        error = PublishError(
            "正式发布按钮尚未点击：" + str(click_result.get("reason") or "扩展未返回派发确认")
        )
        error.result_unknown = click_result.get("dispatched") is not False
        error.click_result = click_result
        if not error.result_unknown:
            error.code = click_result.get("error_code") or "PUBLISH_NOT_DISPATCHED"
        raise error
    if (click_result.get("button_click") or {}).get("received"):
        logger.info("正式发布按钮已收到原生点击: %s", click_result)
    else:
        logger.warning("发布鼠标事件已派发，按钮点击回执缺失，继续核对平台结果: %s", click_result)

    # 3) 轮询等待平台成功响应、成功提示或笔记管理页回读（15s 超时）。
    deadline = time.monotonic() + 15
    observation = None
    while time.monotonic() < deadline:
        observation = _read_publish_observation(
            page,
            expected_title=expected_title,
            initial_url=initial_url,
        )
        verified = _interpret_publish_observation(observation)
        if verified:
            logger.info(
                "发布成功，验证证据=%s 来源=%s",
                verified["evidence"],
                verified["source"],
            )
            verified["click_result"] = click_result
            from .publish_links import complete_publish_link

            return complete_publish_link(
                page,
                verified,
                expected_title,
                observe=lambda: _read_publish_observation(
                    page, expected_title=expected_title, initial_url=initial_url
                ),
            )
        time.sleep(0.3)

    logger.warning("15s 内未取得可验证的发布成功证据")
    return {
        "verified": False,
        "status": "result_unknown",
        "evidence": "none",
        "message": "未取得发布接口成功响应、成功提示或笔记管理页回读",
        "click_result": click_result,
        "final_url": str((observation or {}).get("url") or initial_url),
    }


def _readback_published_note(page: Page, expected_title: str) -> dict:
    """Read the bound account's homepage after platform success has been recorded."""
    from account_identity import load_identity_record
    from product_store import ProductStore

    from .user_profile import get_user_profile

    if not ProductStore().get_setting("publish_profile_readback_enabled", True):
        return {
            "verified": False,
            "status": "disabled_by_policy",
            "message": "按设备规则使用创作中心发布成功证据",
        }
    account = getattr(page, "account", "")
    if not account:
        return {"verified": False, "reason": "当前连接未提供账号槽位"}
    record = load_identity_record(account) or {}
    uid = str((record.get("current") or {}).get("user_id") or "")
    if not uid:
        return {"verified": False, "reason": "槽位未记录 UID"}
    try:
        profile = get_user_profile(page, uid, "")
        matches = [feed for feed in profile.feeds if feed.note_card.display_title == expected_title]
        return {
            "verified": bool(matches),
            "user_id": uid,
            "note_ids": [feed.id for feed in matches],
            "message": "主页已找到本次笔记" if matches else "发布已成功，主页暂未显示本次笔记",
        }
    except Exception as exc:
        return {"verified": False, "user_id": uid, "reason": str(exc)}


def _read_publish_observation(
    page: Page,
    *,
    expected_title: str,
    initial_url: str,
) -> dict:
    script = r"""
        (() => {
            const STORAGE_KEY = '__xhs_publish_result_v1';
            let feedback = window.__xhsPublishResult || null;
            if (!feedback) {
                try {
                    const raw = sessionStorage.getItem(STORAGE_KEY);
                    feedback = raw ? JSON.parse(raw) : null;
                } catch (e) {}
            }
            const url = String(location.href || '');
            const text = (document.body && document.body.innerText) || '';
            const hostPresent = Boolean(
                document.querySelector('xhs-publish-btn[is-publish="true"]')
            );
            const expectedTitle = __EXPECTED_TITLE__;
            const initialUrl = __INITIAL_URL__;
            const onNoteManager = /\/new\/note-manager(?:[/?#]|$)/.test(url);
            return {
                feedback,
                url,
                url_changed: Boolean(initialUrl && url !== initialUrl),
                host_present: hostPresent,
                success_marker: /笔记发布成功|发布成功|发布完成/.test(text),
                note_manager_title_match: Boolean(
                    onNoteManager && expectedTitle && text.includes(expectedTitle)
                ),
            };
        })()
    """
    script = script.replace("__EXPECTED_TITLE__", json.dumps(expected_title, ensure_ascii=False))
    script = script.replace("__INITIAL_URL__", json.dumps(initial_url, ensure_ascii=False))
    result = page.evaluate(script)
    return result if isinstance(result, dict) else {}


def _interpret_publish_observation(observation: dict) -> dict | None:
    feedback = observation.get("feedback")
    if isinstance(feedback, dict):
        source = str(feedback.get("source") or "platform")
        code = feedback.get("code")
        msg = str(feedback.get("msg") or "")
        success = feedback.get("success")
        if code == 0 or success is True:
            evidence = "success_toast" if source == "toast_success" else "platform_response"
            return {
                "verified": True,
                "status": "success",
                "evidence": evidence,
                "source": source,
                "code": code,
                "message": msg or "发布成功",
                "note_id": _find_nested_value(feedback, "note_id", "noteId"),
                "final_url": str(observation.get("url") or ""),
            }

        is_risk_control = (
            (isinstance(code, int) and -9140 <= code <= -9130)
            or "违反" in msg
            or "禁止发笔记" in msg
            or "违规" in msg
        )
        if is_risk_control:
            raise AccountRiskControlError(code or -9136, msg or "账号被风控")
        if code is not None:
            raise PublishError(f"发布失败：code={code} msg={msg!r}")

    if observation.get("note_manager_title_match"):
        return {
            "verified": True,
            "status": "success",
            "evidence": "note_manager_readback",
            "source": "note_manager",
            "code": None,
            "message": "已在创作中心笔记管理页回读到本次标题",
            "note_id": None,
            "final_url": str(observation.get("url") or ""),
        }

    if (
        observation.get("success_marker")
        and observation.get("url_changed")
        and not observation.get("host_present")
    ):
        return {
            "verified": True,
            "status": "success",
            "evidence": "success_page",
            "source": "success_page",
            "code": None,
            "message": "发布后页面显示明确成功提示",
            "note_id": None,
            "final_url": str(observation.get("url") or ""),
        }
    return None


def _find_nested_value(payload: object, *keys: str) -> object | None:
    if isinstance(payload, dict):
        for key in keys:
            value = payload.get(key)
            if value not in (None, ""):
                return value
        for value in payload.values():
            found = _find_nested_value(value, *keys)
            if found not in (None, ""):
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = _find_nested_value(value, *keys)
            if found not in (None, ""):
                return found
    return None


def save_as_draft(page: Page, *, expected_title: str = "") -> dict:
    """点击「暂存离开」，并用成功提示或草稿管理页回读确认结果。"""
    initial_url = str(page.evaluate("location.href") or "")
    page.evaluate(
        """
        (() => {
            if (window.__xhsDraftObs) window.__xhsDraftObs.disconnect();
            window.__xhsDraftResult = null;
            const SUCCESS = /草稿已保存|已保存到草稿箱|暂存成功/;
            const FAILURE = /草稿保存失败|暂存失败|无法保存草稿/;
            const obs = new MutationObserver((mutations) => {
                if (window.__xhsDraftResult) return;
                mutations.forEach((mutation) => {
                    mutation.addedNodes.forEach((node) => {
                        if (node.nodeType !== 1) return;
                        const message = (node.textContent || '').trim();
                        if (!message || message.length > 300) return;
                        if (SUCCESS.test(message)) {
                            window.__xhsDraftResult = {success: true, message};
                        } else if (FAILURE.test(message)) {
                            window.__xhsDraftResult = {success: false, message};
                        }
                    });
                });
            });
            obs.observe(document.body, {childList: true, subtree: true});
            window.__xhsDraftObs = obs;
        })()
        """
    )
    clicked = page.evaluate(
        """
        (() => {
            const buttons = document.querySelectorAll('button.custom-button');
            for (const btn of buttons) {
                if (btn.textContent.trim() === '暂存离开') {
                    btn.click();
                    return true;
                }
            }
            return false;
        })()
        """
    )
    if not clicked:
        raise PublishError("未找到「暂存离开」按钮")

    observation_script = r"""
        (() => {
            const url = String(location.href || '');
            const text = (document.body && document.body.innerText) || '';
            const expectedTitle = __EXPECTED_TITLE__;
            const initialUrl = __INITIAL_URL__;
            const onDraftManager = /\/new\/note-manager(?:[/?#]|$)/.test(url)
                || /draft/i.test(url);
            return {
                feedback: window.__xhsDraftResult || null,
                url,
                manager_title_match: Boolean(
                    initialUrl !== url
                    && onDraftManager
                    && expectedTitle
                    && text.includes(expectedTitle)
                ),
            };
        })()
    """
    observation_script = observation_script.replace(
        "__EXPECTED_TITLE__",
        json.dumps(expected_title, ensure_ascii=False),
    ).replace("__INITIAL_URL__", json.dumps(initial_url, ensure_ascii=False))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        observation = page.evaluate(observation_script) or {}
        feedback = observation.get("feedback") or {}
        if feedback.get("success") is True:
            return {
                "verified": True,
                "status": "saved_draft",
                "evidence": "draft_success_toast",
                "saved_as_draft": True,
                "message": str(feedback.get("message") or "草稿已保存"),
                "final_url": str(observation.get("url") or ""),
            }
        if feedback.get("success") is False:
            raise PublishError(str(feedback.get("message") or "草稿保存失败"))
        if observation.get("manager_title_match"):
            return {
                "verified": True,
                "status": "saved_draft",
                "evidence": "draft_manager_readback",
                "saved_as_draft": True,
                "message": "已在草稿管理页回读到本次标题",
                "final_url": str(observation.get("url") or ""),
            }
        time.sleep(0.3)

    return {
        "verified": False,
        "status": "result_unknown",
        "evidence": "none",
        "saved_as_draft": False,
        "message": "未取得草稿保存成功提示或草稿管理页回读",
        "final_url": initial_url,
    }


# ========== 页面导航 ==========


def _navigate_to_publish_page(page: Page) -> None:
    """按平台打开创作入口。"""
    if getattr(page, "_platform", "xiaohongshu") == "rednote":
        page.navigate(PUBLISH_URL)
        page.wait_for_load(timeout=300)
        page.wait_dom_stable()
        return
    page.navigate(MANAGER_URL)
    page.wait_for_load(timeout=300)
    page.wait_dom_stable()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if _click_visible_publish_text(page, "发布笔记"):
            return
        time.sleep(0.2)
    raise PublishError("未找到创作中心“发布笔记”入口")


def _click_visible_publish_text(page: Page, label: str) -> bool:
    """按当前页面可见文字点击创作入口或发布类型。"""
    point = page.evaluate(
        f"""
        (() => {{
            const label = {json.dumps(label)};
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            while (walker.nextNode()) {{
                const node = walker.currentNode;
                if (node.textContent.trim() !== label) continue;
                const el = node.parentElement;
                if (el.closest('[data-hp-kind],[button-hp-installed],[aria-hidden="true"]'))
                    continue;
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                if (rect.width <= 0 || rect.height <= 0 ||
                    rect.right <= 0 || rect.bottom <= 0 ||
                    rect.left >= innerWidth || rect.top >= innerHeight ||
                    style.display === 'none' || style.visibility === 'hidden')
                    continue;
                return {{x: rect.left + rect.width / 2, y: rect.top + rect.height / 2}};
            }}
            return null;
        }})()
        """
    )
    if not point:
        return False
    page.mouse_click(point["x"], point["y"])
    return True


def _active_publish_tab(page: Page) -> str | None:
    return page.evaluate(
        """
        (() => {
            for (const tab of document.querySelectorAll('div.creator-tab.active')) {
                if (tab.hasAttribute('data-hp-kind') ||
                    tab.hasAttribute('button-hp-installed')) continue;
                const rect = tab.getBoundingClientRect();
                if (rect.left < -1000 || rect.top < -1000) continue;
                return tab.querySelector('span.title')?.textContent.trim() || null;
            }
            return null;
        })()
        """
    )


def _click_publish_tab(page: Page, tab_name: str) -> None:
    """从“发布笔记”菜单选择发布类型并确认对应编辑页。"""
    deadline = time.monotonic() + 15
    clicked = False
    while time.monotonic() < deadline:
        if _active_publish_tab(page) == tab_name:
            return
        if not clicked:
            clicked = _click_visible_publish_text(page, tab_name)
        time.sleep(0.2)
    raise PublishError(f"未能打开“{tab_name}”编辑页")


# ========== 图片上传 ==========


def _upload_images(page: Page, image_paths: list[str]) -> None:
    """逐张上传图片。"""
    import os

    valid_paths = [p for p in image_paths if os.path.exists(p)]
    if not valid_paths:
        raise PublishError("没有有效的图片文件")

    for i, path in enumerate(valid_paths):
        selector = UPLOAD_INPUT if i == 0 else FILE_INPUT
        logger.info("上传第 %d 张图片: %s", i + 1, path)

        page.set_file_input(selector, [path])
        _wait_for_upload_complete(page, i + 1)
        time.sleep(1)


def _wait_for_upload_complete(page: Page, expected_count: int) -> None:
    """等待图片上传完成。"""
    max_wait = 60.0
    start = time.monotonic()

    while time.monotonic() - start < max_wait:
        count = page.get_elements_count(IMAGE_PREVIEW)
        if count >= expected_count:
            logger.info("图片上传完成: %d", count)
            return
        time.sleep(0.5)

    raise UploadTimeoutError(f"第{expected_count}张图片上传超时(60s)")


# ========== 表单提交 ==========


def _extract_hashtags_from_content(content: str, tags: list[str]) -> tuple[str, list[str]]:
    """从正文末尾提取 hashtag 行，合并到 tags 列表。

    Returns:
        (cleaned_content, merged_tags)
    """
    lines = content.rstrip().split("\n")
    # 检查最后一行是否全是 #tag 格式
    if lines:
        last_line = lines[-1].strip()
        hashtag_pattern = re.compile(r"^(#\S+\s*)+$")
        if hashtag_pattern.match(last_line):
            # 提取 hashtag
            extracted = re.findall(r"#(\S+)", last_line)
            # 合并到 tags（去重）
            existing = {t.lstrip("#") for t in tags}
            merged = list(tags)
            for t in extracted:
                if t not in existing:
                    merged.append(t)
                    existing.add(t)
            # 去掉最后一行
            cleaned = "\n".join(lines[:-1]).rstrip()
            logger.info("从正文末尾提取 %d 个标签，合并后共 %d 个", len(extracted), len(merged))
            return cleaned, merged
    return content, list(tags)


def _fill_publish_form(
    page: Page,
    title: str,
    content: str,
    tags: list[str],
    schedule_time: str | None,
    is_original: bool,
    visibility: str,
) -> None:
    """填写表单（不点击发布）。"""
    # 从正文末尾提取 hashtag 并合并到 tags
    content, tags = _extract_hashtags_from_content(content, tags)

    # 标题——填写前先校验长度，超限直接报错（由 AI 重新生成标题）
    from title_utils import calc_title_length

    title_len = calc_title_length(title)
    if title_len > 20:
        raise TitleTooLongError(str(title_len), "20")

    page.input_text(TITLE_INPUT, title)
    time.sleep(0.5)
    _check_title_max_length(page)
    logger.info("标题长度检查通过")
    time.sleep(1)

    # 正文
    content_selector = _find_content_element(page)
    page.input_content_editable(content_selector, content)

    # 回点标题（增强稳定性）
    time.sleep(1)
    page.click_element(TITLE_INPUT)
    logger.info("已回点标题输入框")

    # 标签
    if tags:
        _input_tags(page, content_selector, tags)
    time.sleep(1)
    _check_content_max_length(page)
    logger.info("正文长度检查通过")

    # 定时发布
    if schedule_time:
        _set_schedule_publish(page, schedule_time)

    # 可见范围
    _set_visibility(page, visibility)

    # 原创声明
    if is_original:
        try:
            _set_original(page)
            logger.info("已声明原创")
        except Exception as e:
            logger.warning("设置原创声明失败: %s", e)

    if tags:
        _verify_topics(page, content_selector, tags)
    logger.info("表单填写完成，进入正式发布")


def _find_content_element(page: Page) -> str:
    """查找内容输入框（兼容两种 UI）。"""
    if page.has_element(CONTENT_EDITOR):
        return CONTENT_EDITOR

    # 查找带 placeholder 的 p 元素的 textbox 父元素
    found = page.evaluate(
        """
        (() => {
            const ps = document.querySelectorAll('p');
            for (const p of ps) {
                const placeholder = p.getAttribute('data-placeholder');
                if (placeholder && placeholder.includes('输入正文描述')) {
                    let current = p;
                    for (let i = 0; i < 5; i++) {
                        current = current.parentElement;
                        if (!current) break;
                        if (current.getAttribute('role') === 'textbox') {
                            return 'found';
                        }
                    }
                }
            }
            return '';
        })()
        """
    )
    if found == "found":
        return "[role='textbox']"

    raise PublishError("没有找到内容输入框")


def _check_title_max_length(page: Page) -> None:
    """检查标题长度是否超限。"""
    text = page.get_element_text(TITLE_MAX_SUFFIX)
    if text:
        parts = text.split("/")
        if len(parts) == 2:
            raise TitleTooLongError(parts[0], parts[1])
        raise TitleTooLongError(text, "?")


def _check_content_max_length(page: Page) -> None:
    """检查正文长度是否超限。"""
    text = page.get_element_text(CONTENT_LENGTH_ERROR)
    if text:
        parts = text.split("/")
        if len(parts) == 2:
            raise ContentTooLongError(parts[0], parts[1])
        raise ContentTooLongError(text, "?")


# ========== 标签输入 ==========


def _input_tags(page: Page, content_selector: str, tags: list[str]) -> None:
    """逐个选择平台话题，并确认后续输入保留了之前的话题。"""
    time.sleep(1)
    _focus_content_end(page, content_selector)
    page.evaluate('document.execCommand("insertParagraph", false, null)')
    time.sleep(0.5)

    confirmed: list[str] = []
    for tag in dict.fromkeys(tag.lstrip("#").strip() for tag in tags):
        _focus_content_end(page, content_selector)
        _input_single_tag(page, content_selector, tag)
        confirmed.append(tag)
        _verify_topics(page, content_selector, confirmed)

    # 失焦后再次回读，确认编辑器已提交所有话题节点。
    page.click_element(TITLE_INPUT)
    time.sleep(0.3)
    _verify_topics(page, content_selector, confirmed)


def _focus_content_end(page: Page, content_selector: str) -> None:
    """将光标放在话题节点外，供下一个话题输入。"""
    page.evaluate(
        f"""
        (() => {{
            const el = document.querySelector({json.dumps(content_selector)});
            if (!el) throw new Error('正文编辑器不存在');
            el.focus();
            const range = document.createRange();
            range.selectNodeContents(el.lastElementChild || el);
            range.collapse(false);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
        }})()
        """
    )


def _read_topics(page: Page, content_selector: str) -> list[dict]:
    """读取平台生成的话题节点及 ID，普通井号文本不属于话题。"""
    return (
        page.evaluate(
            f"""
        (() => {{
            const editor = document.querySelector({json.dumps(content_selector)});
            if (!editor) return [];
            return Array.from(editor.querySelectorAll('a[data-topic]')).flatMap(el => {{
                try {{
                    const topic = JSON.parse(el.getAttribute('data-topic'));
                    return topic && topic.id && topic.id !== 'newTopic_Id' && topic.name
                        ? [{{id: String(topic.id), name: topic.name}}] : [];
                }} catch (_) {{ return []; }}
            }});
        }})()
        """
        )
        or []
    )


def _verify_topics(page: Page, content_selector: str, tags: list[str]) -> None:
    names = {topic["name"] for topic in _read_topics(page, content_selector)}
    missing = [tag.lstrip("#").strip() for tag in tags if tag.lstrip("#").strip() not in names]
    if missing:
        error = PublishError("话题尚未生效：" + "、".join(missing))
        error.code = "PUBLISH_TOPICS_NOT_CONFIRMED"
        raise error


def _select_topic_candidate(page: Page, tag: str) -> bool:
    """选择可见的同名话题，保留编辑器光标与平台候选的选择逻辑。"""
    return bool(
        page.evaluate(
            f"""
        (() => {{
            const selector = {json.dumps(f"{TAG_TOPIC_CONTAINER} {TAG_FIRST_ITEM}")};
            const item = Array.from(document.querySelectorAll(selector)).find(el => {{
                const rect = el.getBoundingClientRect();
                const style = getComputedStyle(el);
                const name = el.querySelector('.name')?.textContent.trim().replace(/^#/, '');
                return rect.width > 0 && rect.height > 0 && style.visibility !== 'hidden'
                    && name === {json.dumps(tag)};
            }});
            if (!item) return false;
            // 直接触发该候选的 Vue onClick；保持正文选区，供平台 command 替换 #query。
            item.click();
            return true;
        }})()
        """
        )
    )


def _input_single_tag(page: Page, content_selector: str, tag: str) -> None:
    """输入话题名称、选择同名候选，等待平台生成带 ID 的话题节点。"""
    # 输入 #
    page.type_text("#", delay_ms=0)
    time.sleep(0.3)

    # 逐字输入标签（随机间隔模拟真实输入）
    for char in tag:
        page.type_text(char, delay_ms=0)
        time.sleep(random.uniform(0.05, 0.12))

    # 搜索候选异步更新；精确匹配完整名称后只选择一次。
    clicked = False
    for _ in range(16):
        time.sleep(0.5)
        if _select_topic_candidate(page, tag):
            clicked = True
            break

    if not clicked:
        error = PublishError(f"未找到同名话题候选：{tag}")
        error.code = "PUBLISH_TOPICS_NOT_CONFIRMED"
        raise error

    for _ in range(12):
        time.sleep(0.5)
        if any(topic["name"] == tag for topic in _read_topics(page, content_selector)):
            logger.info("话题已生效: %s", tag)
            return
    _verify_topics(page, content_selector, [tag])


# ========== 定时发布 ==========


def _set_schedule_publish(page: Page, schedule_time: str) -> None:
    """设置定时发布。"""
    from datetime import datetime

    # 解析 ISO8601 时间
    try:
        dt = datetime.fromisoformat(schedule_time)
    except ValueError as e:
        raise PublishError(f"定时发布时间格式错误: {e}") from e

    # 点击定时发布开关
    page.click_element(SCHEDULE_SWITCH)
    time.sleep(0.8)

    # 设置日期时间
    datetime_str = dt.strftime("%Y-%m-%d %H:%M")
    page.select_all_text(DATETIME_INPUT)
    page.input_text(DATETIME_INPUT, datetime_str)
    time.sleep(0.5)

    logger.info("已设置定时发布: %s", datetime_str)


# ========== 可见范围 ==========


def _set_visibility(page: Page, visibility: str) -> None:
    """设置可见范围。"""
    if not visibility or visibility == "公开可见":
        logger.info("可见范围: 公开可见（默认）")
        return

    supported = {"仅自己可见", "仅互关好友可见"}
    if visibility not in supported:
        raise PublishError(
            f"不支持的可见范围: {visibility}，支持: 公开可见、仅自己可见、仅互关好友可见"
        )

    # 点击下拉框
    page.click_element(VISIBILITY_DROPDOWN)
    time.sleep(0.5)

    # 查找并点击目标选项
    clicked = page.evaluate(
        f"""
        (() => {{
            const opts = document.querySelectorAll({json.dumps(VISIBILITY_OPTIONS)});
            for (const opt of opts) {{
                if (opt.textContent.includes({json.dumps(visibility)})) {{
                    opt.click();
                    return true;
                }}
            }}
            return false;
        }})()
        """
    )

    if not clicked:
        raise PublishError(f"未找到可见范围选项: {visibility}")

    logger.info("已设置可见范围: %s", visibility)
    time.sleep(0.2)


# ========== 原创声明 ==========


def _set_original(page: Page) -> None:
    """设置原创声明。"""
    # 查找原创声明卡片并点击开关
    result = page.evaluate(
        f"""
        (() => {{
            const cards = document.querySelectorAll({json.dumps(ORIGINAL_SWITCH_CARD)});
            for (const card of cards) {{
                if (!card.textContent.includes('原创声明')) continue;
                const sw = card.querySelector({json.dumps(ORIGINAL_SWITCH)});
                if (!sw) continue;
                const input = sw.querySelector('input[type="checkbox"]');
                if (input && input.checked) return 'already_on';
                sw.click();
                return 'clicked';
            }}
            return 'not_found';
        }})()
        """
    )

    if result == "already_on":
        logger.info("原创声明已开启")
        return

    if result == "not_found":
        raise PublishError("未找到原创声明选项")

    time.sleep(0.5)

    # 处理确认弹窗
    _confirm_original_declaration(page)


def _confirm_original_declaration(page: Page) -> None:
    """处理原创声明确认弹窗。"""
    time.sleep(0.8)

    # 勾选 checkbox
    page.evaluate(
        """
        (() => {
            const footers = document.querySelectorAll('div.footer');
            for (const footer of footers) {
                if (!footer.textContent.includes('原创声明须知')) continue;
                const cb = footer.querySelector('div.d-checkbox input[type="checkbox"]');
                if (cb && !cb.checked) cb.click();
                return;
            }
        })()
        """
    )
    time.sleep(0.5)

    # 点击声明原创按钮
    result = page.evaluate(
        """
        (() => {
            const footers = document.querySelectorAll('div.footer');
            for (const footer of footers) {
                if (!footer.textContent.includes('声明原创')) continue;
                const btn = footer.querySelector('button.custom-button');
                if (btn) {
                    if (btn.classList.contains('disabled') || btn.disabled) {
                        const cb = footer.querySelector('div.d-checkbox input[type="checkbox"]');
                        if (cb && !cb.checked) cb.click();
                        return 'button_disabled';
                    }
                    btn.click();
                    return 'clicked';
                }
            }
            return 'button_not_found';
        })()
        """
    )

    if result == "button_not_found":
        raise PublishError("未找到声明原创按钮")
    if result == "button_disabled":
        raise PublishError("声明原创按钮仍处于禁用状态")

    logger.info("已成功点击声明原创按钮")
    time.sleep(0.3)
