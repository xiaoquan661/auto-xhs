"""Separate confirmed inbound counts from collection and handling status."""

from __future__ import annotations

from metric_temporality import event_day


def passive_view(metric: dict, events: list[dict], receipts: list[dict], day: str) -> dict:
    progress = metric["collection_progress"]
    complete = progress["complete"]
    observed = metric["observed_new"]
    capabilities = {
        "new_followers": {"collect-operations-metrics", "collect-notifications"},
        "received_likes": {"collect-operations-metrics"},
        "received_comments": {"collect-note-comments", "collect-notifications",
                              "reply-inbox-collect"},
        "received_private_messages": {"collect-private-messages", "reply-inbox-collect"},
    }
    attempts = []
    for receipt in receipts:
        request = receipt.get("task_request") or {}
        capability = receipt.get("capability") or request.get("capability")
        body = receipt.get("business_result") or {}
        # Creator-center note metrics do not inspect account inbound interactions.
        if "creator_manager" in {
            body.get("source"), (request.get("parameters") or {}).get("source")
        }:
            continue
        inspected_channel = metric["kind"] in {
            "received_comments", "received_private_messages"
        }
        if capability in capabilities[metric["kind"]] or (
            capability == "reply-inbox" and inspected_channel
            and body.get("collection_success") is False
        ):
            attempts.append(receipt)
    latest = max(attempts, key=lambda r: r.get("recorded_at") or "", default={})
    failed = latest.get("state") in {"FAILED", "BLOCKED"} or (
        (latest.get("business_result") or {}).get("collection_success") is False
    )
    collection = metric.get("collection") or {}
    legacy_collection = bool(collection) and not (
        collection.get("period_start") and collection.get("period_end")
    )
    has_data = bool(observed or progress["batches"] or legacy_collection)
    code = (
        "complete" if complete else "failed" if failed else "partial"
        if has_data else "not_collected"
    )
    status = {
        "complete": "截至采集时完整" if progress["is_today"] else "当日统计完整",
        "failed": "采集失败",
        "partial": "部分数据待补齐",
        "not_collected": "未采集",
    }[code]
    reason = ""
    if not complete:
        if failed:
            reason = latest.get("result_summary") or latest.get("error_code") or "采集任务未完成"
        elif progress["reasons"]:
            reason = progress["reasons"][0]
        elif progress["batches"]:
            reason = "有效采集区间尚未覆盖完整统计时段"
        elif legacy_collection:
            reason = "已有采集记录，回执缺少可核对的时间范围"
        elif observed:
            reason = "已取得部分互动，全天数量尚未确认"
        else:
            reason = "该日期尚无可用于统计的采集记录"
    selected = [event for event in events if event_day(event) == day]
    pending = [event for event in selected if event.get("handling_state") == "NEW"]
    is_message = metric["kind"] == "received_private_messages"
    is_replyable = is_message or metric["kind"] == "received_comments"
    handling_known = any(event.get("handling_state") for event in selected) or (
        complete and metric["actual"] == 0
    )
    conversation_ids = {
        event.get("object_id") or (event.get("payload") or {}).get("conversation_uid")
        for event in selected
    } - {None, ""}
    return {
        "count": metric["actual"] if complete else observed if observed else None,
        "count_display": (
            str(metric["actual"]) if complete else f"至少 {observed}" if observed else "—"
        ),
        "status": status, "status_code": code, "reason": reason,
        "unit": "人" if metric["kind"] == "new_followers" else "次"
        if metric["kind"] == "received_likes" else "条",
        "last_collected_at": progress["last_collected_at"]
        or (collection.get("last_collected_at") if legacy_collection else None),
        "last_attempt_at": latest.get("recorded_at"),
        "as_of": progress["as_of"], "missing_intervals": progress["missing_intervals"],
        "pending_count": len(pending) if is_replyable and handling_known else None,
        "replied_count": sum(event.get("handling_state") == "REPLIED" for event in selected)
        if is_replyable and handling_known else None,
        "conversation_count": len(conversation_ids) if is_message and conversation_ids else None,
        "undated_count": metric["undated_observations"],
    }
