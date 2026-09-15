"""Carry collection coverage and confirmed replies through the existing outbox."""

from __future__ import annotations


def cycle_observations(cycle: dict, collections: list[dict], replies: list[dict]) -> list[dict]:
    from employee_operations import flatten_observations

    rows: list[dict] = []
    for item in collections:
        task = item.get("task") or {}
        result = item.get("result") or {}
        rows.extend(flatten_observations(result.get("inbound_events") or {}))
        rows.extend(flatten_observations(result.get("snapshots") or {}))
        rows.extend(flatten_observations(result.get("collection_observations") or []))
        if result.get("collection_observations"):
            continue
        channels = {
            "collect-note-comments": ["received_comments"],
            "collect-private-messages": ["received_private_messages"],
            "collect-operations-metrics": ["new_followers", "received_likes"],
        }.get(task.get("capability"), [])
        for kind in channels:
            growth = result.get("growth_notifications") or {}
            if kind in {"new_followers", "received_likes"}:
                event_type = "new_follower" if kind == "new_followers" else "received_like"
                observed = sum(r.get("event_type") == event_type
                               for r in growth.get("events", []))
                coverage = growth.get("coverage") or "UNKNOWN"
                unit = "event"
            else:
                observed = result.get("count")
                coverage = result.get("coverage") or "UNKNOWN"
                unit = "conversation_turn" if kind == "received_private_messages" else "event"
            rows.append({
                "event_type": "collection_observation",
                "platform_event_id": f"{cycle['cycle_id']}:{kind}",
                "account_slot": cycle["account_slot"],
                "occurred_at": cycle.get("finished_at") or cycle["started_at"],
                "payload": {"metric": kind, "collection_state": task.get("state"),
                            "metric_semantics": "collection_observation",
                            "coverage": coverage, "unit": unit,
                            "period_start": result.get("period_start"),
                            "period_end": result.get("period_end"),
                            "undated_count": result.get("undated_count"),
                            "observed_count": observed,
                            "task_id": task.get("task_id"), "cycle_id": cycle["cycle_id"]},
            })
    for reply in replies:
        rows.append({
            "event_type": "outbound_reply",
            "platform_event_id": reply.get("platform_reply_id") or reply["reply_task_id"],
            "account_slot": reply["account_slot"],
            "occurred_at": reply.get("sent_at") or reply.get("updated_at"),
            "payload": {**reply, "cycle_id": cycle["cycle_id"]},
        })
    return rows
