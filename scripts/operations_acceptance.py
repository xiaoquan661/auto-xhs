"""Account-scoped operational acceptance based on platform-confirmed receipts."""

from __future__ import annotations

from collections import Counter
from typing import Any

from employee_operations import business_date

# Each business action requires one confirmed result across the selected accounts.
ACTION_CATALOG = {
    "browse": ("浏览", ["home-engagement", "get-feed-detail"]),
    "search": ("搜索", ["search-feeds"]),
    "like": ("点赞", ["like-feed", "home-engagement", "keyword-engagement"]),
    "favorite": ("收藏", ["favorite-feed"]),
    "comment": ("评论", ["post-comment", "random-comment"]),
    "reply": ("评论回复", ["reply-comment", "reply-inbox"]),
    "friend_request": ("关注博主", ["follow-user"]),
    "publish_image": ("图文发布", ["fill-publish"]),
    "publish_video": ("视频发布", ["fill-publish-video"]),
    "publish_article": ("长文发布", ["long-article"]),
    "private_message": ("私信发送", ["send-private-messages"]),
    "private_reply": ("入站私信回复", ["reply-inbox", "send-private-messages"]),
    "collect_metrics": ("运营指标采集", ["collect-operations-metrics"]),
    "collect_comments": ("评论采集", ["collect-note-comments"]),
    "collect_messages": ("私信采集", ["collect-private-messages"]),
}


def _success(value: dict) -> bool:
    return value.get("success") is True or str(
        value.get("status") or value.get("state") or ""
    ).upper() in {"SUCCESS", "COMPLETED"}


def _verified(value: dict) -> bool:
    return value.get("platform_readback") is True or value.get("verified") is True


def confirmed_counts(result: dict, request: dict) -> Counter[str]:
    """Do not infer a platform write from task SUCCESS, a summary, or a file path."""
    from compound_receipt import confirmed_action_counts

    compound_counts = confirmed_action_counts({"task_request": request, **result})
    if compound_counts is not None:
        return compound_counts
    counts: Counter[str] = Counter()
    if result.get("state") not in {"SUCCESS", "PARTIAL_SUCCESS"}:
        return counts
    capability = result.get("capability") or request.get("capability") or ""
    items = result.get("item_results")
    items = [v for v in items if isinstance(v, dict)] if isinstance(items, list) else []
    body = result.get("business_result")
    body = body if isinstance(body, dict) else {}
    if capability in {"home-engagement", "keyword-engagement"}:
        for item in items:
            browse = item.get("browse") or {}
            like = item.get("like") or (item.get("actions") or {}).get("like") or {}
            counts["browse"] += int(_success(browse))
            counts["like"] += int(
                _success(like) and _verified(like) and like.get("changed") is not False
            )
        return counts
    for action, (_, capabilities) in ACTION_CATALOG.items():
        if capability not in capabilities:
            continue
        if capability == "reply-inbox" and (
            request.get("parameters") or {}
        ).get("acceptance_action") != action:
            continue
        if action.startswith("publish_"):
            from publish_workflow import VERIFIED_PUBLISH_EVIDENCE

            counts[action] = int(
                body.get("verified") is True and body.get("evidence") in VERIFIED_PUBLISH_EVIDENCE
            )
        elif action.startswith("collect_") or action in {"browse", "search"}:
            # A successful empty collection is a successful read, not a new incoming event.
            counts[action] = int(
                result.get("state") == "SUCCESS"
                and result.get("business_result_status") == "observed"
            )
        else:
            if action in {"private_reply", "private_message"}:
                purpose = (request.get("parameters") or {}).get("acceptance_action")
                if not purpose and capability == "send-private-messages":
                    purpose = "private_message"
                if purpose != action:
                    continue
            confirmed = [item for item in items if _success(item) and _verified(item)]
            if action in {"like", "favorite", "friend_request"}:
                confirmed = [item for item in confirmed if item.get("changed") is True]
            counts[action] = len(confirmed)
            if not items and _success(body) and _verified(body):
                counts[action] = int(
                    action not in {"like", "favorite", "friend_request"}
                    or body.get("changed") is True
                )
    return counts


def acceptance_report(state: dict, *, day: str = "", accounts: list[str] | None = None) -> dict:
    from manager_service import ManagerService

    selected = set(
        accounts
        or [
            f"{row.get('machine_id')}:{row.get('account_slot')}"
            for row in state.get("accounts", {}).values()
        ]
    )
    tasks = {row.get("task_id", key): row for key, row in state.get("tasks", {}).items()}
    latest: dict[str, dict] = {}
    for result in state.get("results", {}).values():
        key = ManagerService._result_execution_key(result)
        if key not in latest or ManagerService._result_revision_key(result) > (
            ManagerService._result_revision_key(latest[key])
        ):
            latest[key] = result
    rows: dict[str, dict[str, Any]] = {
        action: {
            "action": action,
            "label": label,
            "target": 1,
            "actual": 0,
            "status": "PENDING",
            "accounts": {},
            "evidence": [],
            "capabilities": capabilities,
        }
        for action, (label, capabilities) in ACTION_CATALOG.items()
    }
    for result in latest.values():
        task = tasks.get(result.get("task_id"), {})
        request = task.get("request") or result.get("task_request") or {}
        machine = result.get("machine_id") or task.get("machine_id") or ""
        slot = result.get("account_slot") or request.get("account_slot") or ""
        account = f"{machine}:{slot}"
        if account not in selected:
            continue
        if day and business_date(result.get("completed_at")) != day:
            continue
        for action, count in confirmed_counts(result, request).items():
            if count < 1:
                continue
            row = rows[action]
            row["actual"] += count
            row["accounts"][account] = row["accounts"].get(account, 0) + count
            row["evidence"].append(
                {
                    "task_id": result.get("task_id"),
                    "account": account,
                    "record_id": result.get("record_id"),
                    "completed_at": result.get("completed_at"),
                    "confirmed_count": count,
                }
            )
            row["status"] = "PASSED"
    settings = state.get("settings", {}).get("operations_acceptance", {})
    deferred = settings.get("deferred_actions", [])
    for action in deferred:
        if action in rows and rows[action]["status"] != "PASSED":
            rows[action]["status"] = "DEFERRED"
    return {
        "success": True,
        "scope": "selected_accounts_total",
        "date": day or "all_history",
        "accounts": sorted(selected),
        "excluded": ["评论加图片", "拉群"],
        "passed": all(r["status"] in {"PASSED", "DEFERRED"} for r in rows.values()),
        "full_scope_passed": all(r["status"] == "PASSED" for r in rows.values()),
        "deferred_actions": deferred,
        "actions": list(rows.values()),
    }
