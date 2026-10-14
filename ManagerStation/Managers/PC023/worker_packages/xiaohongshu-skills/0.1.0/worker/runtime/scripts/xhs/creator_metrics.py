"""Read per-note cumulative counters from creator note management."""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime

from .errors import XHSError
from .metrics_collector import parse_count
from .publish_links import MANAGER_URL, note_link

CARD_SCRIPT = r"""(() => {
    if (!/^https:\/\/creator\.(xiaohongshu|rednote)\.com\/new\/note-manager/.test(location.href))
        return [];
    return [...document.querySelectorAll('.note-card[data-impression]')].map(card => ({
        impression: card.getAttribute('data-impression'),
        title: card.querySelector('.note-card__title')?.textContent.trim() || '',
        text: card.innerText
    }));
})()"""


def parse_creator_card(card: dict, *, platform: str = "xiaohongshu") -> dict | None:
    try:
        impression = json.loads(card.get("impression") or "{}")
        note_id = impression["noteTarget"]["value"]["noteId"]
    except (ValueError, KeyError, TypeError):
        return None
    if not isinstance(note_id, str) or not re.fullmatch(r"[a-fA-F0-9]{24}", note_id):
        return None
    lines = [line.strip() for line in str(card.get("text") or "").splitlines() if line.strip()]
    published = next((i for i, line in enumerate(lines)
                      if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", line)), None)
    # Creator cards display eye, comment, heart, star, share in this order.
    values = lines[published + 1:] if published is not None else []
    fields = ("views", "comments", "likes", "favorites", "shares")
    metrics = dict.fromkeys(fields)
    if len(values) == 5:
        metrics = dict(zip(fields, (parse_count(value) for value in values), strict=True))
    return {
        "entity_id": note_id, **note_link(note_id, platform=platform),
        "title": card.get("title") or "", "metrics": metrics,
        "extra": {"published_at": lines[published] if published is not None else "",
                  "metric_semantics": "cumulative", "source": "creator_manager"},
    }


def collect_creator_metrics(
    page: object, *, note_ids: list[str] | None = None, max_notes: int = 50,
) -> dict:
    targets = set(note_ids or [])
    current_url = str(page.evaluate("location.href"))
    page.navigate(current_url if "/new/note-manager" in current_url else MANAGER_URL)
    page.wait_for_load()
    notes: dict[str, dict] = {}
    unchanged = 0
    for attempt in range(20):
        before = len(notes)
        cards = page.evaluate(CARD_SCRIPT)
        for card in cards if isinstance(cards, list) else []:
            note = parse_creator_card(card, platform=getattr(page, "platform", "xiaohongshu"))
            if note:
                notes[note["entity_id"]] = note
        if targets and targets.issubset(notes):
            break
        if not targets and len(notes) >= max_notes:
            break
        unchanged = unchanged + 1 if before == len(notes) else 0
        if notes and unchanged >= 3:
            break
        if notes:
            page.evaluate("""(() => {
                const card = [...document.querySelectorAll('.note-card')].at(-1);
                card?.scrollIntoView({block: 'end'});
            })()""")
        if attempt < 19:
            time.sleep(1)
    if not notes:
        empty_inventory = page.evaluate("""(() => {
            const text = document.body.innerText;
            return /全部\\s*0(?:\\s|$)/.test(text)
                && text.includes('没有找到相关笔记');
        })()""")
        if empty_inventory and not targets:
            return {
                "captured_at": datetime.now(UTC).isoformat(), "source": "creator_manager",
                "notes": [], "count": 0, "missing_note_ids": [],
                "incomplete_note_ids": [], "partial": False,
                "history_coverage": "visible_notes", "inventory_complete": True,
            }
        raise XHSError("笔记管理未返回可读取的作品，运营数据保持待更新")
    selected = [note for key, note in notes.items() if not targets or key in targets][:max_notes]
    missing = sorted(targets - {note["entity_id"] for note in selected})
    incomplete = [note["entity_id"] for note in selected
                  if any(value is None for value in note["metrics"].values())]
    return {"captured_at": datetime.now(UTC).isoformat(), "source": "creator_manager",
            "notes": selected, "count": len(selected), "missing_note_ids": missing,
            "incomplete_note_ids": incomplete, "partial": bool(missing or incomplete),
            "history_coverage": "requested_notes" if targets else "visible_notes"}
