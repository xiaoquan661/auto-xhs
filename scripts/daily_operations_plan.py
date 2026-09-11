"""Dated account strategy, immutable execution materials and receipt budgets."""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta
from typing import Any

from employee_operations import BUSINESS_TIMEZONE, PLAN_METRICS
from default_persona import friendly_commenter
from service_errors import ServiceError

HARD_METRICS = ("browse", "like", "comment", "reply", "publish")
OPPORTUNITY_METRICS = ("friend_request",)
DEFAULT_POLICY = {
    "start_time": "09:00",
    "end_time": "22:00",
    "action_spacing_seconds": 300,
    "replacement_round_limit": 4,
    "profile_post_cap": 7,
    "process_inbound_comments": True,
    "process_inbound_messages": False,
}


def plan_date(value: Any) -> str:
    try:
        return date.fromisoformat(str(value or "")).isoformat()
    except ValueError as exc:
        raise ServiceError("INVALID_PLAN_DATE", "请选择有效的计划日期") from exc


def normalize_policy(value: dict | None, previous: dict | None = None) -> dict:
    policy = {**DEFAULT_POLICY, **(previous or {})}
    if value is None:
        return policy
    if not isinstance(value, dict):
        raise ServiceError("INVALID_DAILY_POLICY", "每日运营规则应为对象")
    for name in DEFAULT_POLICY:
        if name not in value:
            continue
        item = value[name]
        if name in {"start_time", "end_time"}:
            if not isinstance(item, str) or not re.fullmatch(r"\d{2}:\d{2}", item):
                raise ServiceError("INVALID_DAILY_POLICY", "执行时间应为 HH:MM")
            try:
                time.fromisoformat(item)
            except ValueError as exc:
                raise ServiceError("INVALID_DAILY_POLICY", "执行时间超出有效范围") from exc
        elif name.startswith("process_"):
            if type(item) is not bool:
                raise ServiceError("INVALID_DAILY_POLICY", "入站检查选项应为布尔值")
        elif type(item) is not int or item < 0:
            raise ServiceError("INVALID_DAILY_POLICY", "数量、间隔和替换轮数应为非负整数")
        policy[name] = item
    if policy["start_time"] == policy["end_time"]:
        raise ServiceError("INVALID_DAILY_POLICY", "执行窗口开始和结束时间应不同")
    return policy


def window(day: str, policy: dict) -> dict:
    start = datetime.combine(date.fromisoformat(day), time.fromisoformat(policy["start_time"]))
    end = datetime.combine(date.fromisoformat(day), time.fromisoformat(policy["end_time"]))
    if end <= start:
        end += timedelta(days=1)
    return {
        "timezone": "Asia/Shanghai",
        "start_at": start.replace(tzinfo=BUSINESS_TIMEZONE).isoformat(),
        "end_at": end.replace(tzinfo=BUSINESS_TIMEZONE).isoformat(),
    }


def generate_account(day: str, account: dict, override: dict | None = None) -> dict:
    config = account.get("operations_config") or {}
    source = {**account, **(override or {})}
    stage = str(source.get("stage") or config.get("stage") or "稳定运营")
    pressure = str(source.get("pressure") or config.get("pressure") or "低压")
    participating = account.get("health_state") == "READY"
    participating = participating and source.get("participating", True) is True
    participating = participating and "观察" not in stage
    evidence = account.get("planning_evidence") or {}
    numbers = re.findall(r"\d+", str(evidence.get("comment_range") or ""))
    low, high = (6, 9) if "高压" in pressure else (3, 6)
    if len(numbers) == 2:
        low, high = sorted(map(int, numbers))
    if "第 1 天" in stage or "第1天" in stage:
        low, high = 1, 3
    elif "2–3" in stage or "2-3" in stage:
        low, high = 3, 6
    seed = f"{day}:{account['machine_id']}:{account['account_slot']}"
    pick = sum((index + 1) * ord(char) for index, char in enumerate(seed))
    comments = low + pick % (high - low + 1)
    metrics = {name: None for name in PLAN_METRICS}
    metrics.update(browse=max(12, comments * 3), like=(comments + 1) // 3, comment=comments)
    metrics.update(reply=0, publish=0)
    policy = normalize_policy(config.get("daily_policy"))
    rules = config.get("publish_rules") or {}
    posts = evidence.get("lifetime_posts")
    total_known = evidence.get("posts_scope") in {"profile_total", "account_total", "total"}
    last = evidence.get("last_post_date")
    gap = (date.fromisoformat(day) - date.fromisoformat(last)).days * 1440 if last else None
    interval = rules.get("min_interval_minutes")
    interval = 2880 if interval is None else interval
    reasons = [f"评论区间 {low}–{high}，点赞约为评论的 1/3"]
    if (
        total_known
        and type(posts) is int
        and posts < policy["profile_post_cap"]
        and (posts == 0 or (gap is not None and gap >= interval))
    ):
        metrics["publish"] = (
            min(1, rules.get("daily_limit") or 0) if rules.get("daily_limit") is not None else 1
        )
    if "2–3" in stage or "2-3" in stage:
        metrics["publish"] = 0
    if not total_known:
        reasons.append("主页采样待补全，发帖建议为 0")
    candidates = evidence.get("warm_candidates") or []
    metrics["friend_request"] = min(len(candidates), 2 if "高压" in pressure else 1)
    if not participating:
        metrics = dict.fromkeys(PLAN_METRICS, 0)
        reasons = ["账号当前静默观察"]
    return {
        "machine_id": account["machine_id"],
        "account_slot": account["account_slot"],
        "participating": participating,
        "stage": stage,
        "pressure": pressure,
        "metrics": metrics,
        "notes": "；".join(reasons),
    }


def material(plan: dict, row: dict, account: dict, persona: dict | None) -> dict:
    config = account.get("operations_config") or {}
    policy = normalize_policy(config.get("daily_policy"))
    rules = {
        "daily_limit": 1,
        "min_interval_minutes": 2880,
        **{k: v for k, v in (config.get("publish_rules") or {}).items() if v is not None},
    }
    metrics = copy.deepcopy(row.get("metrics") or {})
    limits = {name: int(metrics.get(name) or 0) for name in HARD_METRICS + OPPORTUNITY_METRICS}
    evidence = copy.deepcopy(account.get("planning_evidence") or {})
    blockers = []
    if account.get("health_state") != "READY":
        blockers.append("账号需要恢复为 READY")
    if not (account.get("current_uid") or account.get("uid")):
        blockers.append("账号需要同步已核验的 UID")
    if not persona:
        persona = friendly_commenter(row.get("machine_id", ""), row.get("account_slot", ""))
    if limits["publish"] > rules["daily_limit"]:
        blockers.append("发帖计划超过账号每日上限")
    if limits["like"] > limits["browse"]:
        blockers.append("点赞数量超过浏览预算，请增加浏览数量或减少点赞")
    pending = []
    if limits["publish"]:
        pending.append(
            {
                "metric": "publish",
                "count": limits["publish"],
                "reason": "准备独立素材，检查主页总量与发帖间隔，预览并确认发布",
            }
        )
    if limits["reply"]:
        pending.append(
            {
                "metric": "reply",
                "count": limits["reply"],
                "reason": "采集真实入站后，按账号回复规则生成和审核回复",
            }
        )
    if limits["friend_request"]:
        pending.append(
            {
                "metric": "friend_request",
                "count": limits["friend_request"],
                "reason": "从暖互动线索读取目标，通过 Agent CLI 预览并执行关注博主",
            }
        )
    return {
        "schema": "auto_xhs_daily_operation_v1",
        "plan_date": plan["plan_date"],
        "plan_version": plan["version"],
        "identity": {
            "machine_id": row["machine_id"],
            "account_slot": row["account_slot"],
            "uid": account.get("current_uid") or account.get("uid") or "",
            "platform": account.get("platform") or "xiaohongshu",
        },
        "persona_snapshot": copy.deepcopy(persona or {}),
        "stage": row.get("stage", ""),
        "pressure": row.get("pressure", ""),
        "budgets": limits,
        "hard_targets": {name: limits[name] for name in HARD_METRICS},
        "opportunity_budgets": {name: limits[name] for name in OPPORTUNITY_METRICS},
        "observation_targets": {k: metrics.get(k) for k in PLAN_METRICS if k not in limits},
        "time_window": window(plan["plan_date"], policy),
        "strategy": {
            **policy,
            "target_selection": "employee_owned",
            "warm_follow_only": True,
            "unknown_result": "isolate_target",
            "publish_confirmation_required": True,
            "exception_notes": config.get("exception_policy", ""),
        },
        "publish_rules": rules,
        "operations_config": copy.deepcopy(config),
        "evidence": evidence,
        "pending_requirements": pending,
        "blockers": blockers,
    }


def remaining_budget(budgets: dict, counts: dict) -> dict:
    return {name: max(0, target - counts.get(name, 0)) for name, target in budgets.items()}
