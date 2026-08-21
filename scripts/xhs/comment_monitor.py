"""Read comments from explicitly tracked own notes without sending replies."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .feed_detail import get_feed_detail
from .user_profile import get_user_profile


def collect_own_note_comments(
    page,
    *,
    owner_user_id: str,
    owner_xsec_token: str = "",
    max_notes: int = 20,
    profile_loader: Callable | None = None,
    detail_loader: Callable | None = None,
) -> dict:
    loader = profile_loader or get_user_profile
    profile = loader(page, owner_user_id, owner_xsec_token)
    tracked_notes = [
        {"feed_id": feed.id, "xsec_token": feed.xsec_token}
        for feed in profile.feeds[:max_notes]
        if feed.id
    ]
    result = collect_note_comments(
        page,
        tracked_notes,
        owner_user_id=owner_user_id,
        detail_loader=detail_loader,
    )
    return {**result, "discovered_note_count": len(tracked_notes)}


def collect_note_comments(
    page,
    tracked_notes: list[dict],
    *,
    owner_user_id: str = "",
    detail_loader: Callable | None = None,
) -> dict:
    loader = detail_loader or get_feed_detail
    comments: list[dict] = []
    failures: list[dict] = []
    newest: tuple[int, str] = (0, "")

    for note in tracked_notes:
        feed_id = str(note.get("feed_id") or "").strip()
        token = str(note.get("xsec_token") or "").strip()
        if not feed_id:
            continue
        try:
            detail = loader(page, feed_id, token)
        except Exception as exc:
            failures.append({"feed_id": feed_id, "message": str(exc)})
            continue

        note_context = {
            "note_title": detail.note.title,
            "note_content": detail.note.body or detail.note.desc,
            "note_tags": list(detail.note.tags),
        }
        for comment in detail.comments.list_:
            for item in _flatten_comment(
                comment,
                feed_id,
                token,
                note_context=note_context,
            ):
                if owner_user_id and item["user_id"] == owner_user_id:
                    continue
                comments.append(item)
                current = (int(item["create_time"] or 0), item["comment_id"])
                if current > newest:
                    newest = current

    return {
        "comments": comments,
        "count": len(comments),
        "tracked_note_count": len(tracked_notes),
        "failed_note_count": len(failures),
        "failures": failures,
        "cursor": f"{newest[0]}:{newest[1]}" if newest[1] else "",
        "last_seen_time": _epoch_to_iso(newest[0]) if newest[0] else None,
        "partial": bool(failures),
    }


def _flatten_comment(
    comment,
    feed_id: str,
    xsec_token: str,
    parent_id: str = "",
    parent_content: str = "",
    note_context: dict | None = None,
):
    occurred_at = _epoch_to_iso(int(comment.create_time or 0))
    yield {
        "comment_id": comment.id,
        "parent_comment_id": parent_id,
        "feed_id": comment.note_id or feed_id,
        "xsec_token": xsec_token,
        "user_id": comment.user_info.user_id,
        "nickname": comment.user_info.nickname or comment.user_info.nick_name,
        "content": comment.content,
        "parent_comment_content": parent_content,
        "create_time": int(comment.create_time or 0),
        "occurred_at": occurred_at,
        **(note_context or {}),
    }
    for child in comment.sub_comments:
        yield from _flatten_comment(
            child,
            feed_id,
            xsec_token,
            parent_id=comment.id,
            parent_content=comment.content,
            note_context=note_context,
        )


def _epoch_to_iso(value: int) -> str:
    if not value:
        return ""
    seconds = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(seconds, UTC).isoformat()
