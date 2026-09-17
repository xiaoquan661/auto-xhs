"""Confirmed per-action counts for compound employee receipts."""

from __future__ import annotations

from collections import Counter

OPERATING_ACTIONS = {
    "browse",
    "like",
    "favorite",
    "comment",
    "friend_request",
    "reply",
    "private_reply",
}
ACTION_ALIASES = {
    "follow": "friend_request",
    "follow-user": "friend_request",
    "inbound-comment-reply": "reply",
    "private-message-reply": "private_reply",
}


def action_items(result: dict) -> list[dict]:
    capability = result.get("capability") or (result.get("task_request") or {}).get("capability")
    if (
        capability not in {"home-engagement", "browser-action"}
        or result.get("item_results_status") == "missing"
    ):
        return []
    items = []
    for item in result.get("item_results") or []:
        if not isinstance(item, dict):
            continue
        action = item.get("action") or item.get("operation")
        action = ACTION_ALIASES.get(action, action)
        if action in OPERATING_ACTIONS:
            items.append({**item, "action": action})
        elif action == "home-engagement" and item.get("status") in {
            "RESULT_UNKNOWN",
            "FAILED",
            "BLOCKED",
        }:
            items.extend({**item, "action": kind} for kind in ("browse", "like"))
    return items


def confirmed_action_counts(result: dict) -> Counter[str] | None:
    body = result.get("business_result") or {}
    if (
        body.get("original_task_id")
        and body.get("reconciliation") == "CONFIRMED_SUCCESS"
        and body.get("new_business_actions") == 0
    ):
        return Counter()
    items = action_items(result)
    if not items:
        return None
    counts: Counter[str] = Counter()
    for item in items:
        action = item["action"]
        status = str(item.get("status") or item.get("state") or "").upper()
        if status in {"RESULT_UNKNOWN", "RECONCILIATION_REQUIRED"}:
            continue
        if status in {"SKIPPED", "FAILED", "BLOCKED", "NOT_SENT"}:
            counts.setdefault(action, 0)
            continue
        success = item.get("success") is True or status in {"SUCCESS", "COMPLETED"}
        verified = (
            action == "browse"
            or item.get("platform_readback") is True
            or item.get("verified") is True
        )
        if success and verified:
            counts[action] += int(action == "browse" or item.get("changed") is not False)
    return counts
