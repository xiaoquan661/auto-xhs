"""Read comment notifications and reply inside the matching notification card."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from .human import sleep_random
from .urls import NOTIFICATION_URL

_SUPPORTED_TYPES = {"comment/item", "comment/comment", "mention/comment"}
_CARD_MARK = "data-auto-xhs-comment-notification"
_REPLY_MARK = "data-auto-xhs-comment-notification-reply"
_EDITOR_MARK = "data-auto-xhs-comment-notification-editor"
_SEND_MARK = "data-auto-xhs-comment-notification-send"


class NotificationReplyError(RuntimeError):
    """A reply failure with explicit knowledge of whether submit was clicked."""

    def __init__(self, message: str, *, result_unknown: bool = False) -> None:
        super().__init__(message)
        self.result_unknown = result_unknown


def collect_comment_notifications(page, *, max_items: int = 50) -> dict:
    """Collect comment/@ notifications from the signed-in web notification page."""

    page.navigate(NOTIFICATION_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(500, 900)

    captured = _wait_for_captured_mentions(page)
    comments = _parse_mentions_response(captured, max_items=max_items)
    source = "notification_api_observation"
    if not comments:
        comments = _extract_notifications_from_dom(page, max_items=max_items)
        source = "notification_dom"

    newest = max(
        ((int(item.get("create_time") or 0), item["notification_id"]) for item in comments),
        default=(0, ""),
    )
    return {
        "comments": comments,
        "count": len(comments),
        "tracked_note_count": 0,
        "failed_note_count": 0,
        "failures": [],
        "cursor": f"{newest[0]}:{newest[1]}" if newest[1] else "",
        "last_seen_time": _epoch_to_iso(newest[0]) if newest[0] else None,
        "partial": False,
        "source": source,
    }


def reply_to_comment_notification(
    page,
    content: str,
    *,
    notification_id: str = "",
    comment_id: str = "",
    nickname: str = "",
    original_content: str = "",
) -> dict:
    """Reply through the notification card selected by its observed content."""

    if not original_content.strip() and not nickname.strip():
        raise NotificationReplyError("通知回复缺少原评论内容和评论者信息")

    page.navigate(NOTIFICATION_URL)
    page.wait_for_load()
    page.wait_dom_stable()
    sleep_random(500, 900)

    match = _find_and_mark_notification(
        page,
        notification_id=notification_id,
        comment_id=comment_id,
        nickname=nickname,
        original_content=original_content,
    )
    if not match.get("found"):
        reason = str(match.get("reason") or "未在通知页找到对应的新评论")
        raise NotificationReplyError(reason)

    reply_selector = f"[{_REPLY_MARK}]"
    page.click_element(reply_selector)
    sleep_random(300, 600)

    editor = _wait_for_editor(page, expected_nickname=nickname)
    if not editor.get("found"):
        raise NotificationReplyError("已找到评论通知，但点击回复后未出现输入框")
    editor_selector = f"[{_EDITOR_MARK}]"
    if editor.get("tag") in {"INPUT", "TEXTAREA"}:
        page.input_text(editor_selector, content)
    else:
        page.input_content_editable(editor_selector, content)
    sleep_random(300, 600)

    submit = _mark_send_button(page, expected_nickname=nickname, expected_content=content)
    if not submit.get("found"):
        raise NotificationReplyError("回复内容已填写，但通知卡片内未出现发送按钮")

    page.click_element(f"[{_SEND_MARK}]")
    acknowledgement = _wait_for_submission_ack(page, content)
    if not acknowledgement:
        raise NotificationReplyError(
            "已点击通知卡片的发送按钮，但未读取到页面确认状态",
            result_unknown=True,
        )
    return {
        "success": True,
        "message": "通知页回复已提交并完成页面回读",
        "readback": acknowledgement,
    }


def _wait_for_captured_mentions(page, timeout: float = 8.0) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = page.evaluate(
            "JSON.stringify(window.__AUTO_XHS_MENTIONS__ || null)"
        )
        if raw:
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                value = None
            if isinstance(value, dict):
                return value
        time.sleep(0.25)
    return None


def _parse_mentions_response(captured: dict | None, *, max_items: int = 50) -> list[dict]:
    if not captured:
        return []
    payload = captured.get("payload") if isinstance(captured.get("payload"), dict) else captured
    data = payload.get("data") if isinstance(payload, dict) else None
    messages = data.get("message_list") if isinstance(data, dict) else None
    if not isinstance(messages, list):
        return []

    parsed: list[dict] = []
    for raw in messages:
        if not isinstance(raw, dict) or str(raw.get("type") or "") not in _SUPPORTED_TYPES:
            continue
        notification_id = str(raw.get("id") or raw.get("notificationId") or "").strip()
        comment_info = raw.get("comment_info") or raw.get("commentInfo") or {}
        item_info = raw.get("item_info") or raw.get("itemInfo") or raw.get("note_info") or {}
        user_info = raw.get("user_info") or raw.get("userInfo") or {}
        if not isinstance(comment_info, dict) or not notification_id:
            continue
        comment_id = str(comment_info.get("id") or comment_info.get("comment_id") or "").strip()
        content = str(comment_info.get("content") or comment_info.get("comment_content") or "").strip()
        if not content:
            continue
        target = comment_info.get("target_comment") or comment_info.get("targetComment") or {}
        create_time = int(raw.get("time") or raw.get("timestamp") or 0)
        parsed.append(
            {
                "notification_id": notification_id,
                "comment_id": comment_id,
                "parent_comment_id": (
                    str(target.get("id") or target.get("comment_id") or "")
                    if isinstance(target, dict) and str(raw.get("type")) == "comment/comment"
                    else ""
                ),
                "feed_id": str(item_info.get("id") or item_info.get("note_id") or ""),
                "xsec_token": str(item_info.get("xsec_token") or ""),
                "user_id": str(
                    user_info.get("userid")
                    or user_info.get("user_id")
                    or user_info.get("userId")
                    or ""
                ),
                "nickname": str(user_info.get("nickname") or ""),
                "content": content,
                "parent_comment_content": (
                    str(target.get("content") or "") if isinstance(target, dict) else ""
                ),
                "note_title": str(item_info.get("content") or ""),
                "note_content": "",
                "note_tags": [],
                "create_time": create_time,
                "occurred_at": _epoch_to_iso(create_time),
                "classification": str(raw.get("type") or ""),
                "source": "notification",
            }
        )
        if len(parsed) >= max_items:
            break
    return parsed


def _extract_notifications_from_dom(page, *, max_items: int) -> list[dict]:
    script = f"""
    (() => {{
      const visible = (el) => {{
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }};
      const text = (el) => String(el?.innerText || el?.textContent || '').trim();
      const relationPhrases = ['评论了你的笔记', '回复了你的评论', '在评论中提到了你'];
      const replyNodes = Array.from(document.querySelectorAll('button,a,[role="button"],.action-reply'))
        .filter((el) => visible(el) && text(el) === '回复');
      const rows = [];
      const seen = new Set();
      for (const reply of replyNodes) {{
        let card = reply;
        for (let depth = 0; card && depth < 9; depth++, card = card.parentElement) {{
          const cardText = text(card);
          if (!relationPhrases.some((phrase) => cardText.includes(phrase))) continue;
          if (cardText.length > 800) continue;
          break;
        }}
        if (!card || seen.has(card)) continue;
        seen.add(card);
        const lines = text(card).split(/\\n+/).map((v) => v.trim()).filter(Boolean);
        const metaIndex = lines.findIndex((line) => relationPhrases.some((phrase) => line.includes(phrase)));
        const ignored = (line) => ['回复', '取消', '发送', '赞', '你的好友'].includes(line) || (/\\d/.test(line) && line.endsWith('前'));
        const content = lines.slice(Math.max(0, metaIndex + 1)).find((line) => !ignored(line) && !line.startsWith('回复 ')) || '';
        const nickname = lines.slice(0, Math.max(0, metaIndex)).find((line) => !ignored(line)) || '';
        const noteLink = Array.from(card.querySelectorAll('a[href*="/explore/"]'))[0];
        const userLink = Array.from(card.querySelectorAll('a[href*="/user/profile/"]'))[0];
        const pathParts = (link) => {{
          try {{ return new URL(link?.href || '').pathname.split('/').filter(Boolean); }}
          catch (_) {{ return []; }}
        }};
        const noteParts = pathParts(noteLink);
        const userParts = pathParts(userLink);
        const exploreIndex = noteParts.indexOf('explore');
        const profileIndex = userParts.indexOf('profile');
        const feedId = exploreIndex >= 0 ? noteParts[exploreIndex + 1] || '' : '';
        const userId = profileIndex >= 0 ? userParts[profileIndex + 1] || '' : '';
        const notificationId = card.getAttribute('data-notification-id') || card.getAttribute('data-id') || '';
        const commentId = card.getAttribute('data-comment-id') || '';
        const timeNode = card.querySelector('time,[data-time],[class*="time"]');
        const rawTime = Number(timeNode?.getAttribute('data-time') || timeNode?.getAttribute('datetime') || 0);
        const stableId = notificationId || ['dom', userId || nickname, feedId, content].join(':');
        if (!content || !stableId) continue;
        rows.push({{
          notification_id: stableId,
          comment_id: commentId,
          parent_comment_id: '',
          feed_id: feedId,
          xsec_token: noteLink ? new URL(noteLink.href).searchParams.get('xsec_token') || '' : '',
          user_id: userId,
          nickname,
          content,
          parent_comment_content: '',
          note_title: '', note_content: '', note_tags: [],
          create_time: Number.isFinite(rawTime) ? rawTime : 0,
          occurred_at: '',
          classification: 'notification_dom',
          source: 'notification'
        }});
        if (rows.length >= {int(max_items)}) break;
      }}
      return rows;
    }})()
    """
    result = page.evaluate(script)
    if not isinstance(result, list):
        return []
    now = datetime.now(UTC).isoformat()
    normalized: list[dict] = []
    for item in result:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if not row.get("occurred_at"):
            row["occurred_at"] = _epoch_to_iso(int(row.get("create_time") or 0)) or now
        normalized.append(row)
    return normalized


def _find_and_mark_notification(
    page,
    *,
    notification_id: str,
    comment_id: str,
    nickname: str,
    original_content: str,
) -> dict:
    for _attempt in range(12):
        result = page.evaluate(
            _mark_notification_script(
                notification_id=notification_id,
                comment_id=comment_id,
                nickname=nickname,
                original_content=original_content,
            )
        )
        if isinstance(result, dict) and result.get("found"):
            return result
        page.evaluate("window.scrollBy(0, Math.max(420, window.innerHeight * 0.7))")
        sleep_random(250, 450)
    return {"found": False, "reason": "未在通知页找到与评论者和原评论内容同时匹配的通知"}


def _mark_notification_script(
    *, notification_id: str, comment_id: str, nickname: str, original_content: str
) -> str:
    values = json.dumps(
        {
            "notificationId": notification_id,
            "commentId": comment_id,
            "nickname": nickname.strip(),
            "content": original_content.strip(),
        },
        ensure_ascii=False,
    )
    return f"""
    (() => {{
      const expected = {values};
      document.querySelectorAll('[{_CARD_MARK}],[{_REPLY_MARK}]').forEach((el) => {{
        el.removeAttribute('{_CARD_MARK}'); el.removeAttribute('{_REPLY_MARK}');
      }});
      const norm = (v) => String(v || '').replace(/\\s+/g, ' ').trim();
      const visible = (el) => {{
        const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
      }};
      const replies = Array.from(document.querySelectorAll('button,a,[role="button"],.action-reply'))
        .filter((el) => visible(el) && norm(el.innerText || el.textContent) === '回复');
      const relationPhrases = ['评论了你的笔记', '回复了你的评论', '在评论中提到了你'];
      const matches = [];
      for (const reply of replies) {{
        let card = reply;
        for (let depth = 0; card && depth < 9; depth++, card = card.parentElement) {{
          const cardText = norm(card.innerText || card.textContent);
          if (!relationPhrases.some((phrase) => cardText.includes(phrase))) continue;
          if (cardText.length > 800) continue;
          const ids = [
            card.getAttribute('data-notification-id'), card.getAttribute('data-id'),
            card.getAttribute('data-comment-id')
          ].filter(Boolean);
          const idMatches = !expected.notificationId && !expected.commentId ||
            ids.includes(expected.notificationId) || ids.includes(expected.commentId);
          const contentMatches = !expected.content || cardText.includes(norm(expected.content));
          const nicknameMatches = !expected.nickname || cardText.includes(norm(expected.nickname));
          if (contentMatches && nicknameMatches && (idMatches || !ids.length)) {{
            matches.push({{card, reply}});
          }}
          break;
        }}
      }}
      const unique = matches.filter((item, index) =>
        matches.findIndex((other) => other.card === item.card) === index
      );
      if (unique.length !== 1) return {{found:false, reason: unique.length ? '匹配到多条相同通知' : '当前可见通知中没有目标评论'}};
      unique[0].card.setAttribute('{_CARD_MARK}', '1');
      unique[0].reply.setAttribute('{_REPLY_MARK}', '1');
      unique[0].card.scrollIntoView({{block:'center'}});
      return {{found:true}};
    }})()
    """


def _wait_for_editor(page, *, expected_nickname: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    nickname = json.dumps(expected_nickname.strip(), ensure_ascii=False)
    while time.monotonic() < deadline:
        result = page.evaluate(
            f"""
            (() => {{
              document.querySelectorAll('[{_EDITOR_MARK}]').forEach((el) => el.removeAttribute('{_EDITOR_MARK}'));
              const card = document.querySelector('[{_CARD_MARK}]');
              if (!card) return {{found:false}};
              const expected = {nickname};
              const editors = Array.from(card.querySelectorAll('textarea,input,[contenteditable="true"]'))
                .filter((el) => {{ const r=el.getBoundingClientRect(); return r.width>0 && r.height>0; }});
              const editor = editors.find((el) => !expected || String(el.getAttribute('placeholder') || '').includes(expected)) || editors[0];
              if (!editor) return {{found:false}};
              editor.setAttribute('{_EDITOR_MARK}', '1');
              return {{found:true, tag:editor.tagName, placeholder:editor.getAttribute('placeholder') || ''}};
            }})()
            """
        )
        if isinstance(result, dict) and result.get("found"):
            return result
        time.sleep(0.2)
    return {"found": False}


def _mark_send_button(page, *, expected_nickname: str, expected_content: str) -> dict:
    values = json.dumps(
        {"nickname": expected_nickname.strip(), "content": expected_content},
        ensure_ascii=False,
    )
    result = page.evaluate(
        f"""
        (() => {{
          document.querySelectorAll('[{_SEND_MARK}]').forEach((el) => el.removeAttribute('{_SEND_MARK}'));
          const card = document.querySelector('[{_CARD_MARK}]');
          const editor = document.querySelector('[{_EDITOR_MARK}]');
          if (!card || !editor) return {{found:false}};
          const expected = {values};
          const placeholder = String(editor.getAttribute('placeholder') || '');
          if (expected.nickname && placeholder && !placeholder.includes(expected.nickname)) return {{found:false}};
          const actual = String(editor.value ?? editor.innerText ?? editor.textContent ?? '').trim();
          if (actual !== String(expected.content).trim()) return {{found:false}};
          const button = Array.from(card.querySelectorAll('button,a,[role="button"]'))
            .find((el) => String(el.innerText || el.textContent || '').trim() === '发送' && !el.disabled);
          if (!button) return {{found:false}};
          button.setAttribute('{_SEND_MARK}', '1');
          return {{found:true}};
        }})()
        """
    )
    return result if isinstance(result, dict) else {"found": False}


def _wait_for_submission_ack(page, content: str, timeout: float = 8.0) -> str:
    expected = json.dumps(content.strip(), ensure_ascii=False)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = page.evaluate(
            f"""
            (() => {{
              const card = document.querySelector('[{_CARD_MARK}]');
              if (!card) return 'card-refreshed';
              const editor = card.querySelector('[{_EDITOR_MARK}]');
              const send = card.querySelector('[{_SEND_MARK}]');
              if (!editor) return 'editor-closed';
              const value = String(editor.value ?? editor.innerText ?? editor.textContent ?? '').trim();
              if (!value && !send) return 'editor-cleared';
              if (String(card.innerText || '').includes({expected}) && !send) return 'reply-rendered';
              return '';
            }})()
            """
        )
        if isinstance(result, str) and result:
            return result
        time.sleep(0.25)
    return ""


def _epoch_to_iso(value: int) -> str:
    if not value:
        return ""
    seconds = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(seconds, UTC).isoformat()
