"""Collect account and own-note metrics from the signed-in user's profile."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .user_profile import get_user_profile


def collect_operations_metrics(
    page,
    *,
    owner_user_id: str,
    owner_xsec_token: str = "",
    max_notes: int = 50,
    profile_loader: Callable | None = None,
) -> dict:
    loader = profile_loader or get_user_profile
    profile = loader(page, owner_user_id, owner_xsec_token)
    captured_at = datetime.now(UTC).isoformat()

    account_metrics: dict[str, int | None] = {
        "followers": None,
        "following": None,
        "likes": None,
        "favorites": None,
        "notes": len(profile.feeds),
    }
    interaction_counts: dict[str, int | None] = {}
    for interaction in profile.interactions:
        label = str(interaction.name or interaction.type or "").strip()
        value = parse_count(interaction.count)
        if label:
            interaction_counts[label] = value
        normalized = label.lower()
        if "粉丝" in label or "fans" in normalized or "followers" in normalized:
            account_metrics["followers"] = value
        elif "关注" in label or "following" in normalized:
            account_metrics["following"] = value
        elif "收藏" in label and "获赞" not in label:
            account_metrics["favorites"] = value
        elif "获赞" in label and "收藏" not in label:
            account_metrics["likes"] = value

    notes: list[dict] = []
    for feed in profile.feeds[:max_notes]:
        if not feed.id:
            continue
        interact = feed.note_card.interact_info
        notes.append(
            {
                "entity_id": feed.id,
                "title": feed.note_card.display_title,
                "metrics": {
                    "likes": parse_count(interact.liked_count),
                    "favorites": parse_count(interact.collected_count),
                    "comments": parse_count(interact.comment_count),
                    "shares": parse_count(interact.shared_count),
                },
                "extra": {"xsec_token": feed.xsec_token},
            }
        )

    return {
        "captured_at": captured_at,
        "account": {
            "entity_id": owner_user_id,
            "metrics": account_metrics,
            "extra": {
                "nickname": profile.user_basic_info.nickname,
                "red_id": profile.user_basic_info.red_id,
                "interaction_counts": interaction_counts,
            },
        },
        "notes": notes,
        "count": len(notes),
        "source": "user_profile",
    }


def parse_count(value) -> int | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    multiplier = 1
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return None
