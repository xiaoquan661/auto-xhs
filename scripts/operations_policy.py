"""Apply saved account operating rules before a dispatched action executes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from publish_workflow import VERIFIED_PUBLISH_EVIDENCE
from service_errors import ServiceError

BUSINESS_TZ = timezone(timedelta(hours=8))
PUBLISH_ACTIONS = {"fill-publish", "fill-publish-video", "long-article", "click-publish"}


def check_daily_context(
    parameters: dict,
    *,
    account: str,
    current_uid: str | None = None,
    now: datetime | None = None,
) -> None:
    """Apply the frozen plan window and account binding at the point of execution."""
    material = parameters.get("daily_plan")
    if not isinstance(material, dict) or not material:
        return
    identity = material["identity"]
    if identity["account_slot"] != account or (
        current_uid is not None and identity.get("uid") and identity["uid"] != current_uid
    ):
        raise ServiceError("DAILY_IDENTITY_CHANGED", "每日计划绑定的账号身份已变化", 409)
    now = now or datetime.now(UTC)
    window = material["time_window"]
    if (
        not datetime.fromisoformat(window["start_at"])
        <= now
        < datetime.fromisoformat(window["end_at"])
    ):
        raise ServiceError("DAILY_WINDOW_CLOSED", "当前不在每日计划执行窗口内", 409)


def check_operations_policy(
    config: dict[str, Any],
    capability: str,
    history: list[dict],
    *,
    now: datetime | None = None,
    account_state: str = "",
    write_intent: bool = True,
) -> None:
    if not config or not write_intent:
        return
    now = (now or datetime.now(UTC)).astimezone(BUSINESS_TZ)
    rule = (config.get("exception_rules") or {}).get(account_state)
    if rule == "pause":
        raise ServiceError(
            "OPERATIONS_ACCOUNT_PAUSED", f"账号状态 {account_state} 触发暂停策略", 409
        )
    windows = config.get("time_windows") or []
    if windows and not any(w["start"] <= now.strftime("%H:%M") <= w["end"] for w in windows):
        raise ServiceError("OPERATIONS_WINDOW_WAIT", "当前不在账号运营时间窗口内", 409)
    if capability not in PUBLISH_ACTIONS:
        return
    rules = config.get("publish_rules") or {}
    published: list[datetime] = []
    for row in history:
        request = row.get("task_request") or row.get("request") or {}
        if (row.get("capability") or request.get("capability")) not in PUBLISH_ACTIONS:
            continue
        state = row.get("business_state") or row.get("state")
        if state in {"RESULT_UNKNOWN", "RECONCILIATION_REQUIRED"}:
            raise ServiceError(
                "OPERATIONS_PUBLISH_UNKNOWN", "前次发布结果待核实，请先完成对账", 409
            )
        result = row.get("business_result") or row.get("result") or {}
        if state != "SUCCESS" or result.get("published") is False:
            continue
        if not result.get("verified") or result.get("evidence") not in VERIFIED_PUBLISH_EVIDENCE:
            continue
        at = row.get("completed_at") or row.get("finished_at") or row.get("recorded_at")
        if at:
            parsed = datetime.fromisoformat(str(at).replace("Z", "+00:00"))
            published.append(
                parsed.replace(tzinfo=BUSINESS_TZ)
                if parsed.tzinfo is None
                else parsed.astimezone(BUSINESS_TZ)
            )
    limit = rules.get("daily_limit")
    if limit is not None and sum(at.date() == now.date() for at in published) >= limit:
        raise ServiceError("OPERATIONS_PUBLISH_LIMIT", "已达到账号当日发帖上限", 409)
    interval = int(rules.get("min_interval_minutes") or 0)
    if published and now < max(published) + timedelta(minutes=interval):
        next_at = max(published) + timedelta(minutes=interval)
        raise ServiceError(
            "OPERATIONS_PUBLISH_WAIT", f"发帖间隔未到，下次可发布时间 {next_at.isoformat()}", 409
        )
