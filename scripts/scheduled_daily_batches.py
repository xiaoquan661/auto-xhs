"""Prepare the entire dated batch timetable before dispatching to employees."""

from __future__ import annotations

import copy
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from math import ceil
from typing import Any

from daily_operations_plan import HARD_METRICS
from service_errors import ServiceError


def prepare_batches(runner: Any, task: dict, now: datetime, remaining: dict) -> list[dict]:
    spec = task["daily_material"]
    identity = spec["identity"]
    policy = spec["strategy"]
    start = datetime.fromisoformat(spec["time_window"]["start_at"])
    end = datetime.fromisoformat(spec["time_window"]["end_at"])
    base = max(start, now)
    times = policy.get("batch_start_times") or []
    actions = []
    budget = dict(remaining)
    count = max(ceil(budget["browse"] / 15), len(times)) if budget["browse"] else 0
    for index in range(count):
        browsed = ceil(budget["browse"] / (count - index))
        if not browsed:
            break
        liked = min(browsed, ceil(budget["like"] / (count - index)))
        actions.append(
            {
                "capability": "home-engagement",
                "write_intent": liked > 0,
                "expected_metrics": {"browse": browsed, "like": liked},
                "parameters": {
                    "browse_count": browsed,
                    "like_count": liked,
                    "duration_minutes": max(10, browsed + liked * 3),
                    "min_read_seconds": 15,
                    "max_read_seconds": 45,
                },
            }
        )
        budget["browse"] -= browsed
        budget["like"] -= liked
    # Collection, intelligent comments and reviewed publishing inputs share this queue.
    linked = []
    while True:
        action = runner.next_action(spec, budget, Counter(), Counter(), linked, set())
        if action is None:
            action, _ = runner.supplied_action(task, budget, Counter(), linked)
        if action is None:
            break
        actions.append(action)
        for kind, amount in action["expected_metrics"].items():
            budget[kind] -= amount
        linked.append({"request": action})
    requests = []
    previous = None
    for index, action in enumerate(actions):
        if index < len(times):
            hour, minute = map(int, times[index].split(":"))
            scheduled = start.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if scheduled < start:
                scheduled += timedelta(days=1)
        else:
            scheduled = (
                base
                if previous is None
                else (
                    datetime.fromisoformat(requests[-1]["parameters"]["scheduled_at"])
                    + timedelta(seconds=policy["action_spacing_seconds"])
                )
            )
        if not start <= scheduled < end or (scheduled < base and times):
            raise ServiceError("BATCH_TIME_INVALID", "批次时间应在剩余执行窗口内")
        action.update(**identity, action_id=f"batch-{index + 1}")
        action["parameters"].update(
            daily_plan=spec,
            daily_expected_metrics={
                metric: int(spec["budgets"].get(metric) or 0) for metric in HARD_METRICS
            },
            batch_expected_metrics=action["expected_metrics"],
            scheduled_at=scheduled.isoformat(),
            expires_at=end.isoformat(),
            batch_index=index + 1,
            batch_count=len(actions),
            batch_interval_seconds=policy["action_spacing_seconds"],
            depends_on_task_ids=[previous] if previous else [],
            excluded_feed_ids=[],
            excluded_user_ids=[identity["uid"]],
            operations_config={
                **spec.get("operations_config", {}),
                "publish_rules": spec["publish_rules"],
            },
            instructions=(
                "本批数量为本次需新增的动作数，前批完成量不能抵扣本批。"
                "daily_expected_metrics 是全天累计目标，batch_expected_metrics 是本批新增目标。"
                "批间隔用于两个批次之间，不是每篇阅读或点赞间隔。"
                "复用本账号前批记录仅用于排除重复目标。"
            ),
        )
        request = runner.agent._task_request(task, action)
        request["timeout_seconds"] = max(600, request["timeout_seconds"])
        request["request_summary"] += f" · 第{index + 1}/{len(actions)}批 · {scheduled:%H:%M}"
        requests.append(request)
        previous = request["task_id"]
    return requests


def advance_batches(
    runner: Any,
    task: dict,
    now: datetime,
    remaining: dict,
    uncertain: Counter,
    linked: list[dict],
    status: Callable[..., dict],
) -> dict:
    manager = runner.manager
    ident = task["operation_task_id"]
    end = datetime.fromisoformat(task["daily_material"]["time_window"]["end_at"])
    requests = task.get("batch_requests")
    if now >= end and not linked:
        manager.finish_operation_task(
            ident,
            {
                "manager_agent_id": runner.agent.agent_id,
                "state": "BLOCKED",
                "result_summary": "执行窗口结束，未执行批次已停止",
            },
        )
        return status("WINDOW_ENDED")
    if requests is None:
        # Old incremental tasks retain their original execution flow until reconciled.
        if linked or any(uncertain.values()):
            return status("WAITING_RECONCILIATION")
        requests = prepare_batches(runner, task, now, remaining)
        manager.record_operation_plan(
            ident,
            {
                "manager_agent_id": runner.agent.agent_id,
                "plan_summary": "完整批次时间表，由员工机本地定时执行",
                "planned_actions": [{"action_id": r["task_id"], **r} for r in requests],
            },
        )
        manager.store.update(
            "operation_tasks", ident, lambda row: {**row, "batch_requests": copy.deepcopy(requests)}
        )
    failures = []
    by_id = {t["task_id"]: t for t in linked}
    for request in requests:
        saved = by_id.get(request["task_id"])
        if saved and saved.get("dispatch_state") not in {"PENDING_DISPATCH", "TRANSFERRED"}:
            continue
        if now >= end:
            break
        try:
            result = manager.dispatch_task(saved["request"] if saved else request)
            if result.get("success") is False:
                failures.append({"task_id": request["task_id"], "status": result.get("status")})
        except Exception as exc:
            failures.append({"task_id": request["task_id"], "message": str(exc)})
    if not requests and any(remaining.values()):
        return status("WAITING_REQUIREMENTS")
    done = all(
        runner.agent._task_state(by_id.get(r["task_id"], {})) in {"SUCCESS", "PARTIAL_SUCCESS"}
        for r in requests
    )
    if done and not any(remaining.get(k, 0) for k in HARD_METRICS) and not any(uncertain.values()):
        manager.finish_operation_task(
            ident,
            {
                "manager_agent_id": runner.agent.agent_id,
                "state": "COMPLETED",
                "result_summary": "全部批次指标已完成并回收",
            },
        )
        return status("COMPLETED")
    if requests and all(
        runner.agent._task_state(by_id.get(r["task_id"], {}))
        in {"SUCCESS", "PARTIAL_SUCCESS", "BLOCKED", "FAILED", "CANCELLED"}
        for r in requests
    ):
        manager.finish_operation_task(
            ident,
            {
                "manager_agent_id": runner.agent.agent_id,
                "state": "BLOCKED",
                "result_summary": "批次已结束，剩余指标见回执",
            },
        )
        return status("BATCHES_INCOMPLETE")
    return status(
        "WAITING_RECONCILIATION" if any(uncertain.values()) else "BATCHES_SCHEDULED",
        success=not failures,
        batches=[
            {
                "task_id": r["task_id"],
                "scheduled_at": r["parameters"]["scheduled_at"],
                "state": by_id.get(r["task_id"], {}).get("state", "DISPATCHING"),
            }
            for r in requests
        ],
        dispatch_failures=failures,
    )
