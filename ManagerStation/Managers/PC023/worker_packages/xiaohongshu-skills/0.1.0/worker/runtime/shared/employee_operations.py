"""Shared receipt helpers with compatibility exports for manager views."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from manager_employee_operations import EmployeeOperations, normalize_config

BUSINESS_TIMEZONE = timezone(timedelta(hours=8))
PLAN_METRICS = (
    "browse",
    "like",
    "comment",
    "reply",
    "private_message",
    "private_reply",
    "publish",
    "friend_request",
    "new_followers",
    "received_likes",
    "received_comments",
    "received_private_messages",
)
ACTION_METRICS = (
    "publish",
    "like",
    "comment",
    "friend_request",
    "private_message",
    "private_reply",
)
EVENT_METRICS = {
    "note_comment": "received_comments",
    "comment_reply": "received_comments",
    "private_message": "received_private_messages",
    "private_message_item": "received_private_messages",
    "new_follower": "new_followers",
    "received_like": "received_likes",
}


def business_date(value: Any) -> str:
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BUSINESS_TIMEZONE)
    return parsed.astimezone(BUSINESS_TIMEZONE).date().isoformat()


def flatten_observations(value: Any) -> list[dict]:
    """Unwrap the local collectors' event batches and metric snapshot envelopes."""
    if isinstance(value, list):
        return [row for item in value for row in flatten_observations(item)]
    if not isinstance(value, dict):
        return []
    if value.get("event_type") or value.get("entity_type"):
        return [value]
    containers = (
        "snapshot",
        "account",
        "notes",
        "events",
        "created_events",
        "existing_events",
        "human_owned_events",
        "inbound_events",
        "snapshots",
        "incremental_data",
        "reconciled_outbound",
        "collection_observations",
    )
    if any(name in value for name in containers):
        return [
            row for name in containers if name in value for row in flatten_observations(value[name])
        ]
    return [value] if value else []




def __getattr__(name: str) -> Any:
    if name in {"EmployeeOperations", "normalize_config"}:
        from manager_employee_operations import EmployeeOperations, normalize_config

        exports = {"EmployeeOperations": EmployeeOperations, "normalize_config": normalize_config}
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ACTION_METRICS",
    "BUSINESS_TIMEZONE",
    "EVENT_METRICS",
    "PLAN_METRICS",
    "EmployeeOperations",
    "business_date",
    "flatten_observations",
    "normalize_config",
]
