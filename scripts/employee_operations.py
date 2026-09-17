"""Employee operations views derived from manager plans and collected receipts."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from collection_coverage import batch_summary, covered_dates, observations_for_result
from metric_temporality import event_day, event_time
from operations_context import event_location, observed_post_date, platform_origin, warm_candidates
from passive_metrics import passive_view
from publish_workflow import PUBLISH_WORKFLOW_CAPABILITIES, VERIFIED_PUBLISH_EVIDENCE
from service_errors import ServiceError

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
ACTION_METRICS = ("publish", "like", "comment", "friend_request", "private_message", "private_reply")
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


def normalize_config(request: dict, previous: dict) -> dict:
    config = dict(previous)
    if "daily_policy" in request:
        from daily_operations_plan import normalize_policy

        config["daily_policy"] = normalize_policy(
            request["daily_policy"], config.get("daily_policy")
        )
    for name in ("stage", "pressure", "publish_audience", "comment_range", "exception_policy"):
        if name in request:
            config[name] = str(request[name] or "").strip()
    if "operating_since" in request:
        value = str(request["operating_since"] or "")
        try:
            config["operating_since"] = date.fromisoformat(value).isoformat() if value else ""
        except ValueError as exc:
            raise ServiceError("INVALID_OPERATING_DATE", "运营起始日期格式应为 YYYY-MM-DD") from exc
    if "publish_rules" in request:
        rules = request["publish_rules"]
        if not isinstance(rules, dict):
            raise ServiceError("INVALID_PUBLISH_RULES", "发帖规则应为对象")
        config["publish_rules"] = dict(config.get("publish_rules") or {})
        for name in ("daily_limit", "min_interval_minutes"):
            if name in rules:
                value = rules[name]
                if value is not None and (type(value) is not int or value < 0):
                    raise ServiceError("INVALID_PUBLISH_RULES", "发帖总量和间隔应为非负整数")
                config["publish_rules"][name] = value
    if "exception_rules" in request:
        rules = request["exception_rules"]
        if not isinstance(rules, dict) or any(
            v not in {"pause", "continue"} for v in rules.values()
        ):
            raise ServiceError("INVALID_EXCEPTION_RULES", "异常策略需指定状态及 pause 或 continue")
        config["exception_rules"] = dict(rules)
    if "time_windows" in request:
        windows = request["time_windows"]
        if not isinstance(windows, list):
            raise ServiceError("INVALID_TIME_WINDOWS", "运营时间窗口应为数组")
        windows = [dict(w) if isinstance(w, dict) else w for w in windows]
        for index, window in enumerate(windows):
            try:
                start = datetime.strptime(window["start"], "%H:%M")
                end = datetime.strptime(window["end"], "%H:%M")
                if start >= end:
                    raise ValueError()
            except (KeyError, ValueError, TypeError) as exc:
                raise ServiceError(
                    "INVALID_TIME_WINDOWS", "时间窗口需使用 HH:MM，结束时间晚于开始时间"
                ) from exc
            window = dict(window)
            window["start"] = start.strftime("%H:%M")
            window["end"] = end.strftime("%H:%M")
            windows[index] = window
        config["time_windows"] = windows
    return config


class EmployeeOperations:
    def __init__(
        self,
        state: dict,
        results: list[dict],
        metric_counts: Callable[[dict], dict],
    ) -> None:
        self.state = state
        self.results = results
        self.metric_counts = metric_counts
        self.plans = []
        for plan in state["daily_plans"].values():
            submissions = plan.get("submissions") or []
            version = submissions[0]["version"] if submissions else plan.get("version")
            effective = next((p for p in [plan, *plan.get("history", [])]
                              if p.get("version") == version), plan)
            self.plans.append(effective)
        self.observations: dict[tuple[str, str], list[dict]] = defaultdict(list)
        unique: dict[tuple, dict] = {}
        imported = state.get("worker_observations", [])
        complete_events = {}
        for source in imported:
            event = source["data"]
            for identifier in (event.get("event_id"), event.get("platform_event_id")):
                if identifier:
                    complete_events[source["machine_id"], source["account_slot"], identifier] = event
        # Historical batches are flattened too, so existing manager data remains usable.
        sources = list(state["incremental_records"].values())
        sources += [
            {
                "machine_id": r.get("machine_id"),
                "account_slot": self.slot(r),
                "data": r.get("incremental_data", []),
                "collected_at": r.get("collected_at", ""),
            }
            for r in results
        ]
        # Some historical receipts retain coverage only in their business result.
        # Reuse explicit collector intervals; receipt success alone proves no interval.
        for result in results:
            body = result.get("business_result") or {}
            task = {**(result.get("task_request") or {}),
                    "task_id": result.get("task_id") or result.get("record_id"),
                    "account_slot": self.slot(result),
                    "capability": result.get("capability")
                    or (result.get("task_request") or {}).get("capability")}
            batches = observations_for_result(
                task, body, result.get("recorded_at") or result.get("created_at") or ""
            )
            if batches:
                sources.append({"machine_id": result.get("machine_id"),
                                "account_slot": self.slot(result), "data": batches})
        for source in [*sorted(sources, key=lambda row: row.get("collected_at") or ""), *imported]:
            for row in flatten_observations(source.get("data")):
                slot = source.get("account_slot") or row.get("account_slot") or ""
                full = complete_events.get((source.get("machine_id"), slot,
                                            row.get("event_id"))) or complete_events.get(
                    (source.get("machine_id"), slot, row.get("platform_event_id")))
                if full and not row.get("payload"):
                    row = {**row, **full}
                identity = (
                    (row.get("comment_id") or (row.get("payload") or {}).get("comment_id")
                     if row.get("event_type") in {"note_comment", "comment_reply"} else None)
                    or row.get("platform_event_id")
                    or row.get("snapshot_id")
                    or row.get("event_id")
                    or row.get("record_id")
                    or json.dumps(row, ensure_ascii=False, sort_keys=True)
                )
                key = (
                    source.get("machine_id"),
                    slot,
                    row.get("event_type") or row.get("entity_type"),
                    identity,
                )
                previous = unique.get(key, {})
                unique[key] = {**previous, **row, "payload": {
                    **(previous.get("payload") or {}), **(row.get("payload") or {})}}
                if event_day(previous) and not event_day(row):
                    unique[key]["occurred_at"] = event_time(previous)["occurred_at"]
                    unique[key]["payload"].update(
                        event_occurred_at=event_time(previous)["occurred_at"],
                        time_scope="platform",
                    )
        for (machine, slot, _, _), row in unique.items():
            self.observations[machine, slot].append(row)

    @staticmethod
    def slot(result: dict) -> str:
        return result.get("account_slot") or (result.get("task_request") or {}).get(
            "account_slot", ""
        )

    @staticmethod
    def result_date(result: dict) -> str:
        request = result.get("task_request") or {}
        source = (request.get("parameters") or {}).get("daily_plan") or {}
        if source.get("plan_date"):
            return str(source["plan_date"])
        return business_date(
            result.get("completed_at")
            or result.get("recorded_at")
            or result.get("created_at")
            or result.get("collected_at")
        )

    def account_results(self, machine: str, slot: str, day: str = "") -> list[dict]:
        return [
            r
            for r in self.results
            if r.get("machine_id") == machine
            and self.slot(r) == slot
            and (not day or self.result_date(r) == day)
        ]

    def plan_row(self, machine: str, slot: str, day: str) -> dict:
        return next(
            (
                row
                for plan in self.plans
                if plan["plan_date"] == day
                for row in plan["accounts"]
                if row["machine_id"] == machine and row["account_slot"] == slot
            ),
            {},
        )

    def action_counts(self, result: dict) -> dict:
        from compound_receipt import confirmed_action_counts

        compound_counts = confirmed_action_counts(result)
        if compound_counts is not None:
            return compound_counts
        capability = result.get("capability") or (result.get("task_request") or {}).get(
            "capability", ""
        )
        if capability in PUBLISH_WORKFLOW_CAPABILITIES:
            body = result.get("business_result") or {}
            return (
                {"publish": 1}
                if (
                    isinstance(body, dict)
                    and body.get("verified")
                    and body.get("evidence") in VERIFIED_PUBLISH_EVIDENCE
                    and result.get("state") == "SUCCESS"
                )
                else {}
            )
        if capability == "follow-user":
            body = result.get("business_result") or {}
            if isinstance(body, dict) and body.get("success") is True:
                return {"friend_request": int(body.get("changed") is True)}
            return {}
        if capability.startswith(
            ("collect-", "prepare-", "generate-", "fill-", "save-", "check-", "list-", "search-")
        ):
            return {}
        if capability == "keyword-engagement":
            items = result.get("item_results")
            if not isinstance(items, list) or result.get("item_results_status") == "missing":
                return {}
            return {
                "like": sum(
                    (item.get("actions") or {}).get("like", {}).get("status") == "success"
                    for item in items
                )
            }
        if capability == "home-engagement" and result.get("item_results_status") == "observed":
            counts = self.metric_counts(result)
            return {"browse": counts.get("browse", 0), "like": counts.get("like", 0)}
        return self.metric_counts(result)

    def day_metrics(self, machine: str, slot: str, day: str) -> dict:
        plan = self.plan_row(machine, slot, day)
        receipts = self.account_results(machine, slot, day)
        rows = self.observations[machine, slot]
        actuals: dict[str, int] = {}
        outbound = [r for r in rows if r.get("event_type") == "outbound_reply"
                    and business_date(r.get("occurred_at")) == day]
        covered_tasks = {(r.get("payload") or {}).get("reply_task_id") for r in outbound}
        for result in receipts:
            if result.get("task_id") in covered_tasks or any(
                r.get("event_type") == "outbound_reply"
                for r in flatten_observations(result.get("incremental_data"))
            ):
                continue
            for kind, count in self.action_counts(result).items():
                actuals[kind] = actuals.get(kind, 0) + count
        for row in outbound:
            payload = row.get("payload") or {}
            kind = "private_reply" if payload.get("channel") == "PRIVATE_MESSAGE_REPLY" else "reply"
            if payload.get("send_state") == "CONFIRMED":
                actuals[kind] = actuals.get(kind, 0) + 1
            elif payload.get("send_state") in {"NOT_SENT", "FAILED"}:
                actuals.setdefault(kind, 0)
        collections = {}
        for row in sorted(rows, key=lambda r: r.get("occurred_at") or ""):
            if (row.get("event_type") == "collection_observation"
                    and business_date(row.get("occurred_at")) == day):
                payload = row.get("payload") or {}
                collections[payload.get("metric")] = {
                    **payload, "last_collected_at": row.get("occurred_at")
                }
        observed_events: dict[str, int] = defaultdict(int)
        for row in rows:
            kind = EVENT_METRICS.get(row.get("event_type"))
            if (
                kind
                and event_day(row) == day
            ):
                observed_events[kind] += 1
        # Observed events are a lower bound until collection covers the entire day.
        for kind in EVENT_METRICS.values():
            actuals.pop(kind, None)
        intervals: dict[str, dict] = {}
        snapshots = sorted(
            [r for r in rows if r.get("entity_type") == "account"],
            key=lambda r: r.get("captured_at") or "",
        )
        for kind, field in (("new_followers", "followers"), ("received_likes", "likes")):
            samples = [r for r in snapshots if isinstance(r.get(field), (int, float))]
            before = [r for r in samples if business_date(r.get("captured_at")) < day]
            during = [r for r in samples if business_date(r.get("captured_at")) == day]
            if before and during and before[-1].get("entity_id") == during[-1].get("entity_id"):
                start, end = before[-1], during[-1]
                # Only consecutive dates describe a daily increment.
                if (
                    business_date(start["captured_at"])
                    == (date.fromisoformat(day) - timedelta(days=1)).isoformat()
                ):
                    intervals[kind] = {
                        "value": end[field] - start[field],
                        "from": start["captured_at"],
                        "to": end["captured_at"],
                        "source": "snapshot_delta",
                    }
        inspections = [r for r in receipts if (r.get("capability") or
                       (r.get("task_request") or {}).get("capability")) == "reply-inbox"]
        latest_inspection = max(inspections, key=lambda r: r.get("recorded_at") or "",
                                default={})
        inspection = latest_inspection.get("business_result") or {}
        inspected_items = latest_inspection.get("item_results") or []
        inspected_channels = {
            item.get("action") for item in inspected_items
            if item.get("action") in {"reply", "private_reply"}
        }
        complete_item_set = (
            latest_inspection.get("state") == "SUCCESS"
            and inspection.get("collection_success") is True
            and inspection.get("frozen_event_count") == len(inspected_items)
            and all(item.get("action") in {"reply", "private_reply"} for item in inspected_items)
        )
        empty_inbox = (
            latest_inspection.get("state") == "SUCCESS"
            and inspection.get("collection_success") is True
            and inspection.get("frozen_event_count") == 0
            and inspection.get("pending_count") == 0
            and inspection.get("failed_count", 0) == 0
            and inspection.get("result_unknown_count", 0) == 0
        )
        metrics = {}
        for kind in PLAN_METRICS:
            target = plan.get("metrics", {}).get(kind)
            if kind in set(EVENT_METRICS.values()):
                target = None
            if target is None and kind == "private_message":
                from manager_service import ManagerService

                targets = {}
                for task_key, task in self.state.get("tasks", {}).items():
                    request = task.get("request") or {}
                    if (task.get("machine_id") == machine
                            and request.get("account_slot") == slot
                            and business_date(task.get("created_at")) == day
                            and ManagerService._result_action_kind({"task_request": request}) == kind):
                        count = ManagerService._result_target_count({"task_request": request})
                        if count is not None:
                            parameters = request.get("parameters") or {}
                            logical_id = parameters.get("logical_action_id") or task_key
                            targets[logical_id] = max(targets.get(logical_id, 0), count)
                if targets:
                    target = sum(targets.values())
            if plan and not plan.get("participating") and kind not in set(EVENT_METRICS.values()):
                target = 0
            actual = actuals.get(kind)
            passive = kind in EVENT_METRICS.values()
            partial = passive
            metrics[kind] = {
                "kind": kind,
                "target": target,
                "actual": actual,
                "incomplete": passive,
                "status": "当日总量待确认"
                if passive
                else "未安排"
                if actual is None and target == 0
                else "等待回执"
                if actual is None
                else "已回传"
                if target is None
                else "完成"
                if actual >= target
                else f"已回传 · 差 {target - actual}" if receipts
                else f"待执行 · 差 {target - actual}",
                "tone": "success"
                if actual is not None and target is not None and actual >= target and not partial
                else "info",
                "observation": intervals.get(
                    kind, {"source": "event_count" if partial else "execution_receipt"}
                ),
                "collection": collections.get(kind),
                "target_source": "manager_plan" if target is not None else None,
                "execution_source": "worker_polling" if kind in {
                    "reply", "private_reply", *EVENT_METRICS.values()
                } else "manager_dispatch",
            }
            if not partial and not (
                actual is not None and target is not None and actual >= target
            ):
                from compound_receipt import action_items
                from manager_service import ManagerService

                states = set()
                for result in receipts:
                    items = action_items(result)
                    if items:
                        states.update(str(item.get("status") or item.get("state") or "").upper()
                                      for item in items if item["action"] == kind)
                    elif ManagerService._result_action_kind(result) == kind:
                        states.add(result.get("state"))
                if not states and actual is None and target != 0 and any(
                    result.get("capability") == "home-engagement"
                    and result.get("state") in {
                        "SUCCESS", "PARTIAL_SUCCESS", "FAILED", "BLOCKED",
                        "CANCELLED", "RESULT_UNKNOWN",
                    }
                    for result in receipts
                ):
                    metrics[kind].update(status="任务已结束 · 未提供逐项结果", tone="warning")
                for execution_state, label in (
                    ("RESULT_UNKNOWN", "执行结果待核对"),
                    ("RECONCILIATION_REQUIRED", "执行结果待核对"),
                    ("BLOCKED", "已阻塞"),
                    ("FAILED", "执行失败"),
                    ("SKIPPED", "已跳过"),
                ):
                    if execution_state in states:
                        metrics[kind].update(status=label, tone="warning")
                        break
            if kind in collections and actual is None:
                collection_state = collections[kind].get("collection_state")
                metrics[kind]["status"] = (
                    "已巡检 · 今日增量待确认" if collection_state == "SUCCESS"
                    else "采集未完成"
                )
            if passive:
                coverage = batch_summary(rows, kind, day)
                samples = [r for r in snapshots
                           if business_date(r.get("captured_at")) <= day]
                latest = samples[-1] if samples else {}
                field = {"new_followers": "followers", "received_likes": "likes"}.get(kind)
                events = [r for r in rows if EVENT_METRICS.get(r.get("event_type")) == kind]
                observed = observed_events.get(kind, 0)
                next_day = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
                metrics[kind].update(
                    metric_semantics="daily_event_count", timezone="Asia/Shanghai",
                    period_start=f"{day}T00:00:00+08:00",
                    period_end=f"{next_day}T00:00:00+08:00",
                    observed_new=observed, coverage="partial" if events else "unknown",
                    current_total=latest.get(field) if field else None,
                    current_total_at=latest.get("captured_at") if field else None,
                    sampled_net_change=intervals.get(kind),
                    newly_discovered=sum(business_date(r.get("discovered_at")) == day
                                         and event_time(r)["time_basis"] != "invalid_observation"
                                         for r in events),
                    undated_observations=sum(not event_day(r)
                                             and event_time(r)["time_basis"]
                                             != "invalid_observation"
                                             for r in events),
                    excluded_observations=sum(event_time(r)["time_basis"] == "invalid_observation"
                                              for r in events),
                    actual_display=f"至少 {observed}" if observed else "待确认",
                    observation={"source": "platform_event_time", "count_basis":
                                 "按平台发生日期去重计数；采样范围内已确认数量，全天总量待确认"},
                )
                metrics[kind]["collection_progress"] = coverage
                if coverage["complete"]:
                    if coverage.get("confirmed_count") is not None:
                        observed = coverage["confirmed_count"]
                        metrics[kind]["observed_new"] = observed
                    metrics[kind].update(actual=observed, actual_display=str(observed),
                                         incomplete=False, coverage="complete", status="已采集")
                else:
                    reason = (coverage["reasons"] or [
                        "历史回执缺少采集区间" if events else "该日期尚无有效采集记录"
                    ])[0]
                    metrics[kind].update(
                        status=reason, actual_display=f"至少 {observed}" if observed else "采集缺口",
                        collection_reason=reason,
                        backfill_required=day < datetime.now(BUSINESS_TIMEZONE).date().isoformat(),
                    )
                metrics[kind].update(
                    daily_new=observed, as_of=coverage["as_of"],
                    day_finalized=coverage["day_finalized"],
                )
                if coverage["is_today"]:
                    # Zero before the first sample is a new daily counter, not a
                    # successful empty collection. Keep actual nullable in that case.
                    metrics[kind]["actual_display"] = str(observed)
                    if coverage["complete"]:
                        metrics[kind].update(status="今日新增 · 已采集", collection_reason="")
                    elif not coverage["batches"]:
                        metrics[kind].update(status="待首次采集", coverage="unknown",
                                             collection_reason="当日尚未采集，计数从零开始")
                    else:
                        metrics[kind].update(status="采集不完整", incomplete=True)
                presentation = passive_view(metrics[kind], events, receipts, day)
                metrics[kind].update(
                    passive_view=presentation,
                    actual_display=presentation["count_display"],
                    collection_reason=presentation["reason"],
                )
            if kind in {"reply", "private_reply"} and not (
                target is not None and actual is not None and actual >= target
            ) and any(
                (row.get("payload") or {}).get("send_state") == "AWAITING_RECONCILIATION"
                and ("private_reply" if (row.get("payload") or {}).get("channel")
                     == "PRIVATE_MESSAGE_REPLY" else "reply") == kind
                for row in outbound
            ):
                metrics[kind].update(status="执行结果待核对", tone="warning")
            channel_empty = empty_inbox or (complete_item_set and kind not in inspected_channels)
            if kind in {"reply", "private_reply"} and channel_empty and actual in (None, 0):
                if metrics[kind]["status"] != "执行结果待核对":
                    metrics[kind].update(actual=0, actual_display="无", status="无", tone="info",
                                         empty_reason="本轮巡检没有待回复内容")
        return metrics

    @staticmethod
    def event_view(event: dict) -> dict:
        payload = event.get("payload") or {}
        kind = event.get("event_type")
        location = event_location(event)
        return {
            **event,
            "actor_name": payload.get("nickname") or payload.get("actor_name"),
            "reply_text": payload.get("content"),
            "message_preview": payload.get("content"),
            "parent_comment_text": payload.get("parent_comment_content"),
            "read_status": payload.get("read_status") or event.get("handling_state"),
            "requires_reply": event.get("handling_state") == "NEW"
            and kind in {"note_comment", "comment_reply", "private_message"},
            **location,
        }

    def enrich_account(self, account: dict, day: str) -> dict:
        machine, slot = account["machine_id"], account["account_slot"]
        config = account.get("operations_config") or {}
        plan = self.plan_row(machine, slot, day)
        observations = self.observations[machine, slot]
        events = sorted(
            [r for r in observations if r.get("event_type")],
            key=lambda r: r.get("occurred_at") or "",
            reverse=True,
        )
        results = self.account_results(machine, slot)
        publish_dates = [
            self.result_date(r) for r in results if self.action_counts(r).get("publish", 0) > 0
        ]
        snapshots = sorted(
            [r for r in observations if r.get("entity_type") == "account"],
            key=lambda r: r.get("captured_at") or "",
        )
        latest = snapshots[-1] if snapshots else {}
        own_uid = account.get("current_uid") or account.get("uid")
        if own_uid:
            snapshots = [r for r in snapshots if r.get("entity_id") == own_uid]
            latest = snapshots[-1] if snapshots else {}
        sampled_posts = [r for r in observations if r.get("entity_type") == "note"]
        publish_dates += [
            observed_post_date((r.get("extra") or {}).get("published_at")) for r in sampled_posts
        ]
        candidates = warm_candidates(events, results)
        candidates = [c for c in candidates if c["user_id"] != own_uid]
        since = config.get("operating_since")
        operating_days = (
            max(0, (date.fromisoformat(day) - date.fromisoformat(since)).days + 1)
            if since
            else None
        )
        persona = next(
            (
                p
                for p in self.state["personas"].values()
                if p.get("machine_id") == machine
                and p.get("account_slot") == slot
                and p.get("status") == "ACTIVE"
            ),
            {},
        )
        recent = [e for e in events if business_date(e.get("occurred_at")) == day]
        private = [e for e in recent if e.get("event_type") == "private_message"]
        enriched = {
            **account,
            "stage": plan.get("stage") or config.get("stage", ""),
            "pressure": plan.get("pressure") or config.get("pressure", ""),
            "operating_days": operating_days,
            "session_count": len({e.get("object_id") for e in private}) if private else None,
            "publish_audience": config.get("publish_audience") or persona.get("audience", ""),
            "publish_rules": config.get("publish_rules", {}),
            "exception_policy": config.get("exception_policy", ""),
            "planning_evidence": {
                "lifetime_posts": latest.get("notes"),
                "posts_scope": (latest.get("extra") or {}).get("posts_scope", "profile_sample"),
                "sampled_post_count": len({r.get("entity_id") for r in sampled_posts}),
                "last_post_date": max(publish_dates, default=""),
                "warm_candidate_count": len(candidates) if events or results else None,
                "warm_candidates": candidates,
                "warm_scope": "collected_history",
                "comment_range": config.get("comment_range", ""),
                "captured_at": latest.get("captured_at"),
            },
        }
        for field, types in (
            ("comment_reply_alerts", {"note_comment", "comment_reply"}),
            ("private_message_events", {"private_message"}),
            ("friend_events", {"friend_request", "friend_accepted", "new_follower"}),
        ):
            enriched[field] = [self.event_view(e) for e in events if e.get("event_type") in types]
        for r in results:
            if "friend_request" not in self.action_counts(r):
                continue
            body = r.get("business_result") or {}
            target = body.get("readback") or body.get("target") or {}
            enriched["friend_events"].append(
                {
                    "event_type": "follow_user",
                    "actor_name": target.get("nickname"),
                    "occurred_at": r.get("completed_at") or r.get("recorded_at"),
                    "note": "已关注" if body.get("changed") else "已关注，状态回读",
                    "profile_url": platform_origin(account.get("platform", "xiaohongshu"))
                    + "/user/profile/"
                    + target["user_id"]
                    if target.get("user_id")
                    else "",
                }
            )
        return enriched

    def report(self, day: str) -> dict:
        plan = next((p for p in self.plans if p["plan_date"] == day), None)
        rows = []
        for entry in plan["accounts"] if plan else []:
            if not entry["participating"]:
                continue
            machine, slot = entry["machine_id"], entry["account_slot"]
            metrics = self.day_metrics(machine, slot, day)
            from daily_operations_plan import HARD_METRICS

            actions = [metrics[k] for k in HARD_METRICS if metrics[k]["target"] is not None]
            known = [m for m in actions if m["actual"] is not None or m["target"] == 0]
            target = sum(m["target"] for m in actions)
            observed = [
                metrics[k]["actual"] for k in HARD_METRICS if metrics[k]["actual"] is not None
            ]
            actual = sum(observed) if observed else None
            completed_target_actions = sum(min(m["actual"] or 0, m["target"]) for m in known)
            complete = len(known) == len(actions) and bool(actions)
            receipts = self.account_results(machine, slot, day)
            failures = sum(
                str(r.get("business_state") or r.get("state"))
                in {"FAILED", "BLOCKED", "RESULT_UNKNOWN", "RECONCILIATION_REQUIRED"}
                for r in receipts
            )
            external = sum(self.external_action_count(r) for r in receipts)
            rows.append(
                {
                    "machine_id": machine,
                    "account_slot": slot,
                    "pressure": entry.get("pressure", ""),
                    "metrics": metrics,
                    "target": target,
                    "credited": completed_target_actions,
                    "remaining": {
                        k: max(0, (metrics[k]["target"] or 0) - (metrics[k]["actual"] or 0))
                        for k in HARD_METRICS
                    },
                    "opportunities": {"friend_request": metrics["friend_request"]},
                    "actual": actual,
                    "exception_count": failures,
                    "receipt_count": len(receipts),
                    "external_actions": external,
                    "completion_rate": round(completed_target_actions / target, 4)
                    if complete and target > 0
                    else None,
                    "status": "已回收" if complete else "等待完整回执",
                }
            )
        groups = []
        for pressure, label in (("高压", "高压组"), ("低压", "低压组")):
            selected = [r for r in rows if r["pressure"] in {pressure, label}]
            rates = [r["completion_rate"] for r in selected if r["completion_rate"] is not None]
            receipts = sum(r["receipt_count"] for r in selected)
            groups.append(
                {
                    "label": label,
                    "account_count": len(selected),
                    "analyzed_count": len(rates),
                    "actual": sum(r["actual"] for r in selected if r["actual"] is not None)
                    if any(r["actual"] is not None for r in selected)
                    else None,
                    "completion_rate": round(sum(rates) / len(rates), 4) if rates else None,
                    "exception_rate": sum(r["exception_count"] for r in selected) / receipts
                    if receipts
                    else None,
                }
            )
        comparable = all(g["completion_rate"] is not None for g in groups)
        conclusion = (
            (
                f"高压组平均完成率 {groups[0]['completion_rate']:.0%}，"
                f"低压组 {groups[1]['completion_rate']:.0%}。"
            )
            if comparable
            else "等待两组完整回执"
        )
        return {
            "date": day,
            "plan_version": plan.get("version") if plan else None,
            "has_plan": plan is not None,
            "rows": rows,
            "groups": groups,
            "conclusion": conclusion,
            "exception_count": sum(r["exception_count"] for r in rows),
            "planned_machines": len({r["machine_id"] for r in rows}),
            "executed_machines": len({r["machine_id"] for r in rows if r["receipt_count"]}),
            "target": sum(r["target"] for r in rows),
            "actual": sum(r["actual"] for r in rows if r["actual"] is not None)
            if any(r["actual"] is not None for r in rows)
            else None,
        }

    def external_action_count(self, result: dict) -> int:
        items = result.get("item_results") or []
        if items:
            return sum(
                sum(
                    self.action_counts(
                        {
                            **result,
                            "item_results": [item],
                            "actual_count": None,
                            "completed_count": None,
                        }
                    ).get(k, 0)
                    for k in ACTION_METRICS
                )
                for item in items
                if item.get("execution_source", result.get("execution_source")) == "human"
            )
        return (
            sum(self.action_counts(result).get(k, 0) for k in ACTION_METRICS)
            if result.get("execution_source") == "human"
            else 0
        )

    def collection_records(self) -> list[dict]:
        """Expose the same deduplicated observations used by account daily metrics."""
        records = []
        for (machine, slot), rows in self.observations.items():
            for row in rows:
                timing = event_time(row)
                records.append({
                    **self.event_view(row), "machine_id": machine, "account_slot": slot,
                    "recorded_at": timing["occurred_at"] or row.get("captured_at")
                    or timing["observed_at"] or row.get("created_at"),
                    "time_basis": timing["time_basis"], "statistics_date": event_day(row),
                })
        snapshots = {
            (r["machine_id"], r["account_slot"], r.get("entity_id"), r.get("captured_at"))
            for r in records if r.get("entity_type") == "note"
        }
        for result in self.results:
            business = result.get("business_result") or {}
            if business.get("source") != "creator_manager":
                continue
            for note in business.get("notes") or []:
                machine, slot = result.get("machine_id"), self.slot(result)
                captured = note.get("captured_at") or business.get("captured_at")
                key = (machine, slot, note.get("entity_id"), captured)
                if key in snapshots:
                    continue
                snapshots.add(key)
                records.append({
                    **note, "machine_id": machine, "account_slot": slot, "entity_type": "note",
                    "captured_at": captured, "recorded_at": captured, "time_basis": "collected",
                })
        return records

    def summary(self, accounts: list[dict], day: str | None = None) -> dict:
        today = datetime.now(UTC).astimezone(BUSINESS_TIMEZONE).date()
        day = day or today.isoformat()
        daily_metrics = []
        for account in accounts:
            machine, slot = account["machine_id"], account["account_slot"]
            dates = {day}
            dates.update(
                p["plan_date"]
                for p in self.plans
                if any(
                    row["machine_id"] == machine and row["account_slot"] == slot
                    for row in p["accounts"]
                )
            )
            dates.update(self.result_date(r) for r in self.account_results(machine, slot))
            dates.update(
                business_date(r.get("occurred_at") or r.get("captured_at"))
                for r in self.observations[machine, slot]
            )
            for observation in self.observations[machine, slot]:
                dates.update(covered_dates(observation))
                occurred_day = event_day(observation)
                if occurred_day:
                    dates.add(occurred_day)
            daily_metrics.extend(
                {
                    "machine_id": machine,
                    "account_slot": slot,
                    "date": d,
                    "metrics": self.day_metrics(machine, slot, d),
                }
                for d in sorted(dates)
                if d
            )
        return {
            "success": True,
            "date": day,
            "timezone": "Asia/Shanghai",
            "accounts": [self.enrich_account(a, day) for a in accounts],
            "daily_metrics": daily_metrics,
            "collection_records": self.collection_records(),
            "daily_report": self.report((date.fromisoformat(day) - timedelta(days=1)).isoformat()),
        }
