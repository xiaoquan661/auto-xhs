"""Capture short-lived platform comment submission notices in place."""

from __future__ import annotations

import json
import uuid
from contextlib import suppress

from .cdp import Page
from .errors import ActionResultUnknownError, XHSError
from .selectors import COMMENT_SUBMIT_BUTTON

_ARM_NOTICE = r"""
(() => {
  const key = __KEY__;
  const selector = '[role="alert"], [role="status"], [class*="toast"], [class*="Toast"], '
    + '[class*="message"], [class*="Message"]';
  const visible = el => el.isConnected && el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
  const initial = new Map(Array.from(document.querySelectorAll(selector))
    .filter(visible).map(el => [el, (el.innerText || el.textContent || '').trim()]));
  const state = { result: null, observer: null, timer: null, resolve: null };
  const finish = result => {
    if (state.result) return;
    state.result = result;
    state.observer.disconnect();
    clearTimeout(state.timer);
    if (state.resolve) state.resolve(result);
  };
  const inspect = node => {
    const el = node.nodeType === 1 ? node : node.parentElement;
    if (!el) return;
    const candidates = [el.matches(selector) ? el : null, el.closest(selector),
      ...el.querySelectorAll(selector)].filter(Boolean);
    for (const candidate of candidates) {
      if (!visible(candidate)) continue;
      const text = (candidate.innerText || candidate.textContent || '').trim();
      if (initial.get(candidate) === text) continue;
      const success = /^(评论成功|评论发布成功|发布评论成功|评论发送成功)[！!。]?$/.test(text);
      const failure = /^(评论失败|评论发布失败|发布评论失败|评论发送失败)[！!。]?$/.test(text);
      if (success || failure) {
        finish({status: success ? 'success' : 'failed', text,
          observed_at: new Date().toISOString(), url: location.href});
        return;
      }
    }
  };
  state.observer = new MutationObserver(records => {
    for (const record of records) {
      inspect(record.target);
      for (const node of record.addedNodes || []) inspect(node);
    }
  });
  state.observer.observe(document.documentElement,
    {subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ['class', 'style', 'hidden']});
  state.timer = setTimeout(() => finish({status: 'unknown'}), 8000);
  window[key] = state;
  return true;
})()
"""

_READ_NOTICE = r"""
(() => {
  const state = window[__KEY__];
  if (!state) return {status: 'unknown'};
  if (state.result) return state.result;
  return new Promise(resolve => { state.resolve = resolve; });
})()
"""


class CommentRejectedError(XHSError):
    """The platform displayed an explicit comment submission failure."""

    result_unknown = False


def submit_comment_with_receipt(page: Page, *, feed_id: str, content: str) -> dict:
    """Observe before submitting once; wait at most eight seconds without scrolling."""
    key = "__xhs_comment_notice_" + uuid.uuid4().hex
    encoded = json.dumps(key)
    page.evaluate(_ARM_NOTICE.replace("__KEY__", encoded))
    try:
        page.click_element(COMMENT_SUBMIT_BUTTON)
        receipt = page.evaluate(_READ_NOTICE.replace("__KEY__", encoded))
    except Exception as exc:
        raise ActionResultUnknownError("评论提交结果待核对；请勿重复发送") from exc
    finally:
        with suppress(Exception):
            page.evaluate(
                f"(() => {{ const s=window[{encoded}]; if(s) {{ s.observer.disconnect(); "
                f"clearTimeout(s.timer); delete window[{encoded}]; }} }})()"
            )
    if isinstance(receipt, dict) and receipt.get("status") == "failed":
        raise CommentRejectedError(str(receipt.get("text") or "评论发送失败"))
    if not isinstance(receipt, dict) or receipt.get("status") != "success":
        raise ActionResultUnknownError("8秒内未捕获评论成功提示，结果待核对；请勿重复发送")
    return {
        "success": True,
        "message": "已捕获平台评论成功提示",
        "feed_id": feed_id,
        "content": content,
        "platform_readback": True,
        "confirmation_source": "platform_success_notice",
        "submission_id": key.removeprefix("__xhs_comment_notice_"),
        "platform_receipt": receipt,
    }
