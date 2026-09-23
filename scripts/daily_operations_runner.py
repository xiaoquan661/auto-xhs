"""Incremental daily-plan execution through the existing manager/worker transport."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from comment_receipt_review import pending_comment_attribution
from daily_operations_plan import HARD_METRICS, remaining_budget
from employee_operations import EmployeeOperations

FINAL = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "BLOCKED", "CANCELLED", "RESULT_UNKNOWN"}
UNCERTAIN = {"RESULT_UNKNOWN", "RECONCILIATION_REQUIRED"}


class DailyOperationsRunner:
    def __init__(self, agent: Any) -> None:
        self.agent = agent
        self.manager = agent.manager

    def tick(self, task: dict, now: datetime | None = None) -> dict:
        now = now or datetime.now(UTC)
        identifier = task["operation_task_id"]
        if task["state"] == "WAITING_AGENT":
            task = self.manager.claim_operation_task(
                identifier, {"manager_agent_id": self.agent.agent_id}
            )["operation_task"]
        spec = task["daily_material"]
        identity, policy = spec["identity"], spec["strategy"]
        start = datetime.fromisoformat(spec["time_window"]["start_at"])
        end = datetime.fromisoformat(spec["time_window"]["end_at"])
        linked = self.agent._linked_execution_tasks(identifier)
        # Use the same receipt/reconciliation chain as ordinary manager operations.
        if linked:
            for execution in linked:
                if self.agent._task_state(execution) == "WAITING_APPROVAL":
                    self.manager.reconcile_task(execution["task_id"])
            self.manager.collect_results(identity["machine_id"], download_evidence=True)
            linked = self.agent._linked_execution_tasks(identifier)
        current_results = self.manager.list_results()["current_results"]
        results = [
            r
            for r in current_results
            if (
                r.get("operation_task_id") or (r.get("task_request") or {}).get("operation_task_id")
            )
            == identifier
        ]
        view = EmployeeOperations(
            self.manager.store.snapshot(), results, self.manager._result_metric_counts
        )
        counts: Counter[str] = Counter(spec.get("baseline_actual") or {})
        excluded: set[str] = set()
        for result in results:
            counts.update(view.action_counts(result))
            for row in result.get("item_results") or []:
                if row.get("feed_id"):
                    excluded.add(str(row["feed_id"]))
        remaining = remaining_budget(spec["budgets"], counts)
        uncertain: Counter[str] = Counter()
        failures: Counter[str] = Counter()
        for execution in linked:
            state = self.agent._task_state(execution)
            expected = ((execution.get("request") or {}).get("parameters") or {}).get(
                "daily_expected_metrics", {}
            )
            if state in UNCERTAIN or execution.get("reconciliation_required"):
                credited = Counter()
                for result in results:
                    if result.get("task_id") == execution["task_id"]:
                        credited.update(view.action_counts(result))
                uncertain.update({k: max(0, v - credited[k]) for k, v in expected.items()})
            elif state in {"FAILED", "BLOCKED", "PARTIAL_SUCCESS"}:
                failures.update({k: 1 for k, v in expected.items() if v})
        session = {
            "remaining": remaining,
            "actual": dict(counts),
            "reserved_unknown": dict(uncertain),
            "excluded_feed_ids": sorted(excluded),
            "pending_requirements": [
                {**item, "count": remaining[item["metric"]]}
                for item in spec["pending_requirements"] if remaining[item["metric"]] > 0
            ],
        }

        def status(value: str, **extra: Any) -> dict:
            current = {**session, "status": value, **extra}
            if current != task.get("daily_session"):
                self.manager.store.update(
                    "operation_tasks", identifier, lambda row: {**row, "daily_session": current}
                )
            return {"success": True, "status": value, **current}

        def dispatch(request: dict) -> dict:
            result = self.manager.dispatch_task(request)
            if result.get("success") is False:
                execution = result.get("task") or {}
                return status(
                    str(result.get("status") or "RETRY_PENDING"),
                    success=False,
                    last_action_id=request["task_id"],
                    error_code=execution.get("error_code") or "",
                    message=execution.get("message") or result.get("message") or "",
                )
            return status("DISPATCHED", last_action_id=request["task_id"])

        pending_attribution = [
            r["record_id"] for r in current_results
            if r.get("machine_id") == identity["machine_id"]
            and (r.get("account_slot") or (r.get("task_request") or {}).get("account_slot"))
            == identity["account_slot"]
            and pending_comment_attribution(r)
        ]
        if pending_attribution:
            return status(
                "WAITING_RECONCILIATION", pending_attribution=pending_attribution,
                message="评论已提交，账号归属待核对，暂停后续派发",
            )

        if policy.get("dispatch_mode") == "employee_schedule":
            from scheduled_daily_batches import advance_batches

            return advance_batches(self, task, now, remaining, uncertain, linked, status)

        # A running action is collected before expiry is finalized.
        active = [
            e
            for e in linked
            if not e.get("reconciliation_required")
            and self.agent._task_state(e) not in FINAL | UNCERTAIN | {"WAITING_APPROVAL"}
        ]
        if active:
            pending = next(
                (
                    e
                    for e in active
                    if self.agent._task_state(e) in {"PENDING_DISPATCH", "TRANSFERRED"}
                ),
                None,
            )
            if pending and start <= now < end:
                return dispatch(pending["request"])
            return status("WAITING_RECEIPT")
        receipt_ids = {r.get("task_id") for r in results}
        if any(
            e["task_id"] not in receipt_ids
            and self.agent._task_state(e) in {"SUCCESS", "PARTIAL_SUCCESS"}
            for e in linked
        ):
            return status("WAITING_RECEIPT")
        if now < start:
            return status("WAITING_WINDOW", next_action_at=start.isoformat())
        if now >= end:
            complete = all(remaining[k] == 0 for k in HARD_METRICS)
            self.manager.finish_operation_task(
                identifier,
                {
                    "manager_agent_id": self.agent.agent_id,
                    "state": "COMPLETED" if complete else "BLOCKED",
                    "result_summary": "每日计划逐项完成"
                    if complete
                    else "执行窗口结束，剩余预算见会话对账",
                },
            )
            return status("COMPLETED" if complete else "WINDOW_ENDED")
        account = (
            self.manager.store.get(
                "accounts", f"{identity['machine_id']}:{identity['account_slot']}"
            )
            or {}
        )
        current_uid = account.get("current_uid") or account.get("uid") or ""
        if account.get("health_state") != "READY" or account.get("scheduling_enabled") is False:
            return status("WAITING_ACCOUNT")
        if identity.get("uid") and current_uid != identity["uid"]:
            return status("IDENTITY_CHANGED")
        latest = max(
            (r.get("completed_at") or r.get("recorded_at") or "" for r in results), default=""
        )
        latest = latest or max((e.get("created_at") or "" for e in linked), default="")
        if latest:
            due = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            due += timedelta(seconds=policy["action_spacing_seconds"])
            if now < due:
                return status("WAITING_SPACING", next_action_at=due.isoformat())
        action = self.next_action(spec, remaining, uncertain, failures, linked, excluded)
        if action is None:
            action, input_messages = self.supplied_action(task, remaining, uncertain, linked)
            session["input_messages"] = input_messages
        if action is None:
            if all(remaining[k] == 0 for k in HARD_METRICS) and not any(uncertain.values()):
                self.manager.finish_operation_task(
                    identifier,
                    {
                        "manager_agent_id": self.agent.agent_id,
                        "state": "COMPLETED",
                        "result_summary": "每日硬指标已完成，关注博主单独记账",
                    },
                )
                return status("COMPLETED")
            if any(self.agent._task_state(e) == "WAITING_APPROVAL" for e in linked):
                return status("WAITING_APPROVAL")
            return status(
                "WAITING_RECONCILIATION" if any(uncertain.values()) else "WAITING_REQUIREMENTS"
            )
        action.update(
            machine_id=identity["machine_id"],
            account_slot=identity["account_slot"],
            platform=identity["platform"],
            action_id=f"daily-{len(linked) + 1}",
        )
        action["parameters"].update(
            daily_plan=spec,
            daily_expected_metrics=action["expected_metrics"],
            excluded_feed_ids=sorted(excluded),
            excluded_user_ids=[identity["uid"]] if identity.get("uid") else [],
            operations_config={
                **spec.get("operations_config", {}),
                "publish_rules": spec["publish_rules"],
            },
        )
        request = self.agent._task_request(task, action)
        request["platform"] = identity["platform"]
        request["timeout_seconds"] = min(
            max(600, request["timeout_seconds"]), max(1, (end - now).total_seconds())
        )
        # Preserve the action before dispatch so a restart reuses its exact request.
        self.manager.record_operation_plan(
            identifier,
            {
                "manager_agent_id": self.agent.agent_id,
                "plan_summary": "按剩余预算批量派发",
                "planned_actions": [
                    *(item for item in (task.get("plan") or {}).get("planned_actions") or []
                      if item.get("action_id") != action["action_id"]),
                    action,
                ],
            },
        )
        from service_errors import ServiceError

        try:
            return dispatch(request)
        except ServiceError as exc:
            if exc.code in {"OPERATIONS_WINDOW_WAIT", "OPERATIONS_PUBLISH_WAIT"}:
                return status("WAITING_POLICY", message=exc.message)
            raise

    def supplied_action(
        self, task: dict, remaining: dict, uncertain: Counter, linked: list[dict]
    ) -> tuple[dict | None, list[str]]:
        """Reuse the manager's publish/reply/follow planner for reviewed input items."""
        spec = task["daily_material"]
        identity = spec["identity"]
        key = f"{identity['machine_id']}:{identity['account_slot']}"
        inputs = (task.get("action_inputs") or {}).get(key) or {}
        messages = []
        for metric in ("publish", "reply", "friend_request"):
            if remaining[metric] <= uncertain[metric]:
                continue
            used = {
                ((e.get("request") or {}).get("parameters") or {}).get("daily_input_index")
                for e in linked
                if ((e.get("request") or {}).get("parameters") or {}).get("daily_input_metric")
                == metric
            }
            for index, entry in enumerate(inputs.get(metric) or []):
                if index in used:
                    continue
                plan = self.agent._build_plan(
                    {
                        "targets": task["targets"],
                        "goal_snapshot": {"target_metrics": {metric: 1}},
                        "action_inputs": {key: {metric: [entry]}},
                    }
                )
                if plan["unsupported_metrics"]:
                    messages.extend(plan["unsupported_metrics"])
                    continue
                action = plan["planned_actions"][0]
                action["parameters"].update(daily_input_metric=metric, daily_input_index=index)
                return action, messages
        return None, messages

    @staticmethod
    def next_action(
        spec: dict,
        remaining: dict,
        uncertain: Counter,
        failures: Counter,
        linked: list[dict],
        excluded: set[str],
    ) -> dict | None:
        policy = spec["strategy"]
        done = {(e.get("request") or {}).get("capability") for e in linked}
        for enabled, capability in (
            ("process_inbound_comments", "collect-note-comments"),
            ("process_inbound_messages", "collect-private-messages"),
        ):
            if policy[enabled] and capability not in done:
                return {
                    "capability": capability,
                    "parameters": {},
                    "expected_metrics": {},
                    "write_intent": False,
                }
        if policy.get("collect_work_metrics", False) and "collect-operations-metrics" not in done:
            return {
                "capability": "collect-operations-metrics",
                "parameters": {},
                "expected_metrics": {},
                "write_intent": False,
            }
        available = {
            k: max(0, v - uncertain[k]) if failures[k] <= policy["replacement_round_limit"] else 0
            for k, v in remaining.items()
        }
        attempts: Counter[str] = Counter()
        for execution in linked:
            attempts.update(
                ((execution.get("request") or {}).get("parameters") or {}).get(
                    "daily_expected_metrics", {}
                )
            )
        for name in available:
            if attempts[name] >= spec["budgets"][name] + policy["replacement_round_limit"]:
                available[name] = 0
        if available["browse"]:
            batch_count = ceil(available["browse"] / 15)
            browse_count = ceil(available["browse"] / batch_count)
            like_count = min(browse_count, ceil(available["like"] / batch_count))
            return {
                "capability": "home-engagement",
                "write_intent": like_count > 0,
                "expected_metrics": {"browse": browse_count, "like": like_count},
                "parameters": {
                    "browse_count": browse_count,
                    "like_count": like_count,
                    "duration_minutes": max(10, browse_count + like_count * 3),
                    "min_read_seconds": 15,
                    "max_read_seconds": 45,
                },
            }
        if available["comment"]:
            comment_count = min(3, available["comment"])
            return {
                "capability": "post-comment",
                "write_intent": True,
                "expected_metrics": {"comment": comment_count},
                "parameters": {
                    "count": comment_count,
                    "candidate_pool_size": 10,
                    "collection_minutes": 2,
                    "style": "natural",
                    "direct_send_authorized": True,
                },
            }
        return None
