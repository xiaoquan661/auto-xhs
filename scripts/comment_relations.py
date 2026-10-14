"""Typed projections shared by historical migration and current receipt writes."""

from __future__ import annotations

import json
import sqlite3


def project_comment(connection: sqlite3.Connection, event_id: str) -> None:
    row = connection.execute(
        "SELECT * FROM inbound_events WHERE event_id = ?", (event_id,)
    ).fetchone()
    if row is None or row["event_type"] != "note_comment":
        return
    payload = json.loads(row["payload_json"] or "{}")
    values = [
        payload.get("comment_id"),
        payload.get("feed_id") or row["object_id"],
        payload.get("parent_comment_id"),
        payload.get("nickname"),
        payload.get("content"),
        payload.get("parent_comment_content"),
        payload.get("target_url"),
    ]
    values = [str(value) if value not in (None, "") else None for value in values]
    # Notification IDs remain event identities; they are not platform comment IDs.
    detail_state = "AVAILABLE" if values[0] and values[1] and values[4] else "PARTIAL"
    if not any(values):
        detail_state = "MISSING"
    connection.execute(
        """UPDATE inbound_events SET comment_id=?, note_id=?, parent_comment_id=?,
        author_name=?, content=?, parent_content=?, target_url=?, detail_state=?,
        last_seen_at=COALESCE(last_seen_at, discovered_at) WHERE event_id=?""",
        (*values, detail_state, event_id),
    )


def send_projection(state: str, readback: dict) -> tuple[str, str | None]:
    comment = readback.get("comment") or {}
    if not isinstance(comment, dict):
        comment = {}
    reply_id = readback.get("reply_id") or comment.get("id") or readback.get("message_id")
    confirmed = any(
        readback.get(key) is True
        for key in (
            "reply_found",
            "reply_present",
            "verified",
            "matched",
            "outgoing_message_present",
        )
    )
    if state == "SUCCESS" and (confirmed or reply_id):
        return "CONFIRMED", str(reply_id) if reply_id else None
    if state == "RESULT_UNKNOWN" or state == "SUCCESS":
        return "AWAITING_RECONCILIATION", None
    if state in {"BLOCKED", "CANCELLED", "WAITING_APPROVAL", "QUEUED"}:
        return "NOT_SENT", None
    if state == "FAILED":
        return "FAILED", None
    return "UNKNOWN", None


def project_reply(connection: sqlite3.Connection, task_id: str) -> None:
    row = connection.execute(
        "SELECT * FROM reply_records WHERE reply_task_id=?", (task_id,)
    ).fetchone()
    if row is None:
        return
    readback = json.loads(row["readback_json"] or "{}")
    state, reply_id = send_projection(row["result_state"], readback)
    connection.execute(
        """UPDATE reply_records SET send_state=?, platform_reply_id=?,
        confirmed_at=CASE WHEN ?='CONFIRMED' THEN COALESCE(confirmed_at, updated_at)
        ELSE NULL END WHERE reply_task_id=?""",
        (state, reply_id, state, task_id),
    )


def backfill_relations(connection: sqlite3.Connection) -> None:
    for row in connection.execute("SELECT event_id FROM inbound_events").fetchall():
        project_comment(connection, row["event_id"])
    for row in connection.execute("SELECT reply_task_id FROM reply_records").fetchall():
        project_reply(connection, row["reply_task_id"])
