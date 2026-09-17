"""Recover published note links from responses and creator note management."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from urllib.parse import urlparse

MANAGER_URL = "https://creator.xiaohongshu.com/new/note-manager"


def note_link(note_id: object, url: object = "", *, platform: str = "xiaohongshu") -> dict:
    value = str(note_id or "").strip()
    link = str(url or "").strip()
    parsed = urlparse(link)
    match = re.fullmatch(r"/(?:explore|discovery/item)/([\w-]+)/?", parsed.path)
    if parsed.hostname in {"www.xiaohongshu.com", "www.rednote.com"} and match:
        if value and value != match[1]:
            return {}
        return {"note_id": match[1], "note_url": link}
    if value:
        origin = "https://www.rednote.com/discovery/item" if platform == "rednote" else (
            "https://www.xiaohongshu.com/explore"
        )
        return {"note_id": value, "note_url": f"{origin}/{value}"}
    return {}


def creator_candidates(page: object, title: str) -> list[dict]:
    script = r"""(() => {
        const manager = /^https:\/\/creator\.(xiaohongshu|rednote)\.com\/new\/note-manager/;
        if (!manager.test(location.href))
            return [];
        const title = __TITLE__;
        const results = [];
        for (const card of document.querySelectorAll('.note-card[data-impression]')) {
            if (card.querySelector('.note-card__title')?.textContent.trim() !== title) continue;
            try {
                const impression = JSON.parse(card.getAttribute('data-impression'));
                const id = impression?.noteTarget?.value?.noteId;
                if (typeof id === 'string' && /^[a-f\d]{24}$/i.test(id)) {
                    results.push({note_id: id, note_url: ''});
                }
            } catch (_) {}
        }
        for (const anchor of document.querySelectorAll('a[href]')) {
            const url = new URL(anchor.href, location.href);
            const id = url.pathname.match(/^\/(?:explore|discovery\/item)\/([\w-]+)\/?$/)?.[1];
            if (!id) continue;
            const card = anchor.closest('[data-note-id], .note-item, .note-card') || anchor;
            const texts = [anchor.textContent.trim(), ...Array.from(
                card.querySelectorAll('[title], .title, .note-title')
            ).map(el => (el.getAttribute('title') || el.textContent).trim())];
            if (texts.includes(title)) results.push({note_id: id, note_url: url.href});
        }
        // Only title-bearing note records in the manager page state are candidates.
        const seen = new WeakSet();
        function visit(value, depth = 0) {
            if (!value || typeof value !== 'object' || depth > 12 || seen.has(value)) return;
            seen.add(value);
            if ((value.title || value.displayTitle || value.display_title) === title) {
                const id = value.noteId || value.note_id || value.id;
                if (typeof id === 'string' && /^[a-f\d]{24}$/i.test(id)) {
                    results.push({note_id: id, note_url: value.note_url || value.shareUrl || ''});
                }
            }
            for (const item of Object.values(value)) visit(item, depth + 1);
        }
        visit(window.__INITIAL_STATE__);
        return results;
    })()"""
    raw = page.evaluate(script.replace("__TITLE__", json.dumps(title, ensure_ascii=False)))
    unique = {}
    for candidate in raw if isinstance(raw, list) else []:
        link = note_link(candidate.get("note_id"), candidate.get("note_url"),
                         platform=getattr(page, "platform", "xiaohongshu"))
        if link:
            unique[link["note_id"]] = link
    return list(unique.values())


def lookup_creator_link(page: object, title: str) -> dict:
    if not title.strip():
        return {"link_status": "pending", "link_reason": "原发布任务缺少标题"}
    try:
        page.navigate(MANAGER_URL)
        page.wait_for_load()
        for attempt in range(11):
            matches = creator_candidates(page, title)
            if len(matches) == 1:
                return {**matches[0], "link_status": "resolved", "link_source": "creator_manager",
                        "link_reason": ""}
            if len(matches) > 1:
                return {"link_status": "ambiguous", "link_reason": "发现多个同名作品，需进一步核对"}
            if attempt < 10:
                time.sleep(2)
        return {"link_status": "pending", "link_reason": "创作中心尚未返回唯一作品链接"}
    except Exception as exc:
        return {"link_status": "pending", "link_reason": str(exc)}


def complete_publish_link(
    page: object, result: dict, title: str, *, observe: Callable[[], dict] | None = None
) -> dict:
    from .publish import _find_nested_value

    result = dict(result)
    link = note_link(result.get("note_id"), result.get("note_url"),
                     platform=getattr(page, "platform", "xiaohongshu"))
    if not link and observe:
        for _ in range(3):
            time.sleep(0.5)
            try:
                feedback = observe().get("feedback") or {}
            except Exception:
                break
            if feedback.get("code") == 0 or feedback.get("success") is True:
                link = note_link(
                    _find_nested_value(feedback, "note_id", "noteId"),
                    _find_nested_value(feedback, "note_url", "shareUrl"),
                    platform=getattr(page, "platform", "xiaohongshu"),
                )
                if link:
                    break
    if link:
        result.update(link, link_status="resolved", link_reason="")
        result.setdefault("link_source", "platform_response")
    else:
        result.update(lookup_creator_link(page, title))
    return result
