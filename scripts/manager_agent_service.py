"""常驻经理 Agent：自动领取、规划、派发并核销 WebUI 运营任务。"""

from __future__ import annotations

import math
import os
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

try:
    from .manager_service import ManagerService, ServiceError
except ImportError:  # Direct execution from the scripts directory.
    from manager_service import ManagerService, ServiceError

FINAL_OPERATION_STATES = {"COMPLETED", "BLOCKED"}
FINAL_TASK_STATES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "BLOCKED",
    "RESULT_UNKNOWN",
    "CANCELLED",
    "COMPLETED",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _allocated_count(total: int, index: int, size: int) -> int:
    if total <= 0 or size <= 0:
        return 0
    quotient, remainder = divmod(total, size)
    return quotient + (1 if index < remainder else 0)


class ManagerAgentService:
    """Drive the manager-to-executor lifecycle independently of page requests."""

    def __init__(
        self,
        manager: ManagerService,
        *,
        agent_id: str | None = None,
        interval_seconds: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds is not None and interval_seconds <= 0:
            raise ValueError("经理Agent轮询间隔必须大于0")
        self.manager = manager
        self.agent_id = str(
            agent_id or os.getenv("XHS_MANAGER_AGENT_ID") or "MANAGER-AGENT-01"
        ).strip()
        if not self.agent_id:
            raise ValueError("经理Agent编号不能为空")
        self._interval_seconds = interval_seconds
        self._monotonic = monotonic
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._last_memory_review: float | None = None

    @property
    def interval_seconds(self) -> float:
        if self._interval_seconds is not None:
            return float(self._interval_seconds)
        return float(self.manager.polling_settings["agent_seconds"])

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="manager-agent",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
        thread.join(timeout=max(0.0, timeout_seconds))
        with self._lifecycle_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def run_cycle(self) -> dict[str, Any]:
        started_at = utc_now()
        memory_review = {"status": "WAITING_CYCLE"}
        if (
            self._last_memory_review is None
            or self._monotonic()
            >= self._last_memory_review + self.manager.polling_settings["memory_review_seconds"]
        ):
            try:
                memory_review = self.manager.memory_action("review-pending", {})
            except Exception as exc:
                memory_review = {"status": "RETRY_PENDING", "message": str(exc)}
            self._last_memory_review = self._monotonic()
        outcomes: list[dict[str, Any]] = []
        tasks = self.manager.list_operation_tasks().get("operation_tasks") or []
        tasks.sort(key=lambda item: item.get("created_at") or "")
        for task in tasks:
            if task.get("state") in FINAL_OPERATION_STATES:
                continue
            operation_task_id = str(task.get("operation_task_id") or "")
            try:
                outcome = self._process_operation(task)
                if outcome.get("success") and task.get("last_agent_error"):
                    self.manager.store.update(
                        "operation_tasks",
                        operation_task_id,
                        lambda row: {**row, "last_agent_error": None},
                    )
            except Exception as exc:
                outcome = {
                    "success": False,
                    "status": "FAILED",
                    "error_code": type(exc).__name__,
                    "message": str(exc),
                }
                self.manager.store.update(
                    "operation_tasks",
                    operation_task_id,
                    lambda row, error=outcome: {
                        **row,
                        "last_agent_error": {**error, "recorded_at": utc_now()},
                    },
                )
                self._message(
                    operation_task_id,
                    from_agent=self.agent_id,
                    to_agent="WEBUI",
                    message_type="AGENT_ERROR",
                    status="FAILED",
                    summary=str(exc),
                )
            outcomes.append({"operation_task_id": operation_task_id, **outcome})

        failed = sum(item.get("success") is False for item in outcomes)
        status = "IDLE" if not outcomes else "SUCCESS" if not failed else "PARTIAL_SUCCESS"
        if outcomes and failed == len(outcomes):
            status = "FAILED"
        completed_at = utc_now()
        summary = {
            "success": failed == 0,
            "status": status,
            "agent_id": self.agent_id,
            "started_at": started_at,
            "completed_at": completed_at,
            "next_run_at": (
                datetime.now(UTC) + timedelta(seconds=self.interval_seconds)
            ).isoformat(),
            "interval_seconds": self.interval_seconds,
            "processed": len(outcomes),
            "failed": failed,
            "outcomes": outcomes,
            "memory_review": memory_review,
        }
        self.manager.record_sync_status("agent", summary)
        return summary

    def _run(self) -> None:
        last_cycle = None
        while not self._stop_event.is_set():
            if last_cycle is None or self._monotonic() >= last_cycle + self.interval_seconds:
                self.run_cycle()
                last_cycle = self._monotonic()
            remaining = max(0.05, last_cycle + self.interval_seconds - self._monotonic())
            self._stop_event.wait(min(1.0, remaining))

    def _process_operation(self, task: dict[str, Any]) -> dict[str, Any]:
        if task.get("daily_material"):
            from daily_operations_runner import DailyOperationsRunner

            return DailyOperationsRunner(self).tick(task)
        operation_task_id = str(task["operation_task_id"])
        plan_date = task.get("plan_date")
        if (
            plan_date
            and datetime.now(UTC).astimezone(timezone(timedelta(hours=8))).date().isoformat()
            < plan_date
        ):
            return {"success": True, "status": "WAITING_DATE"}
        if task.get("state") == "WAITING_AGENT":
            self._message(
                operation_task_id,
                from_agent="WEBUI",
                to_agent=self.agent_id,
                message_type="TASK_AVAILABLE",
                status="WAITING_AGENT",
                summary=str(task.get("title") or operation_task_id),
            )
            claimed = self.manager.claim_operation_task(
                operation_task_id,
                {"manager_agent_id": self.agent_id},
            )["operation_task"]
            self._message(
                operation_task_id,
                from_agent=self.agent_id,
                to_agent="WEBUI",
                message_type="TASK_CLAIMED",
                status="PLANNING",
                summary="经理Agent已领取任务并开始规划",
            )
            task = claimed

        plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
        if not plan.get("planned_actions"):
            plan = self._build_plan(task)
            if plan["unsupported_metrics"]:
                summary = "当前经理Agent无法自动拆解指标：" + "、".join(plan["unsupported_metrics"])
                self.manager.finish_operation_task(
                    operation_task_id,
                    {
                        "manager_agent_id": self.agent_id,
                        "state": "BLOCKED",
                        "result_summary": summary,
                    },
                )
                self._message(
                    operation_task_id,
                    from_agent=self.agent_id,
                    to_agent="WEBUI",
                    message_type="PLAN_BLOCKED",
                    status="BLOCKED",
                    summary=summary,
                )
                return {"success": False, "status": "BLOCKED", "message": summary}
            task = self.manager.record_operation_plan(
                operation_task_id,
                {
                    "manager_agent_id": self.agent_id,
                    "plan_summary": plan["summary"],
                    "planned_actions": plan["planned_actions"],
                },
            )["operation_task"]
            self._message(
                operation_task_id,
                from_agent=self.agent_id,
                to_agent="WEBUI",
                message_type="PLAN_READY",
                status="PLANNING",
                summary=plan["summary"],
                payload={"action_count": len(plan["planned_actions"])},
            )

        planned_actions = list((task.get("plan") or {}).get("planned_actions") or [])
        occupied_accounts: set[tuple[str, str]] = set()
        for action in planned_actions:
            account_key = (str(action["machine_id"]).upper(), str(action["account_slot"]))
            if account_key in occupied_accounts:
                continue
            try:
                outcome = self._advance_action(task, action)
            except Exception as exc:
                outcome = {
                    "success": False,
                    "status": "RETRY_PENDING",
                    "error_code": exc.code if isinstance(exc, ServiceError) else type(exc).__name__,
                    "message": str(exc),
                }
            if outcome["status"] not in FINAL_TASK_STATES:
                occupied_accounts.add(account_key)
            self._record_action_outcome(operation_task_id, action, outcome)

        refreshed = self.manager.get_operation_task(operation_task_id)["operation_task"]
        linked_tasks = self._linked_execution_tasks(operation_task_id)
        if any(
            item.get("reconciliation_required") or self._task_state(item) == "RESULT_UNKNOWN"
            for item in linked_tasks
        ):
            return {"success": False, "status": "RECONCILIATION_REQUIRED"}
        dispatch_failures = (refreshed.get("plan") or {}).get("dispatch_failures") or []
        # Match each planned action to its execution, never add failure counts to task counts.
        executions = {item["task_id"]: item for item in linked_tasks}
        expected_ids = {self._action_task_id(task, action) for action in planned_actions}
        if expected_ids - executions.keys() or any(
            self._task_state(executions[identifier]) not in FINAL_TASK_STATES
            for identifier in expected_ids & executions.keys()
        ):
            return {
                "success": not dispatch_failures,
                "status": "RETRY_PENDING" if dispatch_failures else str(refreshed["state"]),
            }

        progress = refreshed.get("progress") or {}
        target_metrics = progress.get("target_metrics") or {}
        actual_metrics = progress.get("actual_metrics") or {}
        missing = {
            name: int(target) - int(actual_metrics.get(name) or 0)
            for name, target in target_metrics.items()
            if int(actual_metrics.get(name) or 0) < int(target)
        }
        state = "COMPLETED" if not missing and not dispatch_failures else "BLOCKED"
        if dispatch_failures:
            summary = "部分员工任务派发失败：" + "；".join(
                f"{item['account_slot']}：{item['message']}" for item in dispatch_failures
            )
        elif missing:
            summary = "执行已结束，目标仍缺：" + "、".join(
                f"{name} {count}" for name, count in missing.items()
            )
        else:
            summary = "员工Agent结果已回收，运营目标指标已完成"
        self.manager.finish_operation_task(
            operation_task_id,
            {
                "manager_agent_id": self.agent_id,
                "state": state,
                "result_summary": summary,
            },
        )
        self._message(
            operation_task_id,
            from_agent=self.agent_id,
            to_agent="WEBUI",
            message_type="TASK_FINISHED",
            status=state,
            summary=summary,
            payload={"target_metrics": target_metrics, "actual_metrics": actual_metrics},
        )
        return {"success": state == "COMPLETED", "status": state, "message": summary}

    @staticmethod
    def _action_task_id(task: dict[str, Any], action: dict[str, Any]) -> str:
        return (
            f"{task['operation_task_id'].lower()}-{action['machine_id'].lower()}-"
            f"{str(action['action_id']).lower()}"
        )

    def _advance_action(self, task: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        task_id = self._action_task_id(task, action)
        existing = self._find_execution_task(task_id)
        if existing is not None:
            state = self._task_state(existing)
            if existing.get("reconciliation_required") or state == "RESULT_UNKNOWN":
                return {
                    "success": False,
                    "status": "RECONCILIATION_REQUIRED",
                    "message": "原任务结果待核对，保持当前动作",
                }
            if state == "WAITING_APPROVAL":
                self.manager.reconcile_task(task_id)
                existing = self._find_execution_task(task_id) or existing
                state = self._task_state(existing)
            if existing.get("reconciliation_required") or state == "RESULT_UNKNOWN":
                return {
                    "success": False,
                    "status": "RECONCILIATION_REQUIRED",
                    "message": "原任务结果待核对，保持当前动作",
                }
            if state not in {"PENDING_DISPATCH", "TRANSFERRED"}:
                return {
                    "success": state not in {"FAILED", "BLOCKED", "RESULT_UNKNOWN"},
                    "status": state,
                    "message": existing.get("message") or "",
                    "error_code": existing.get("error_code") or "",
                }
            request = existing["request"]
        else:
            request = self._task_request(task, action)
        self._message(
            task["operation_task_id"],
            task_id=task_id,
            from_agent=self.agent_id,
            to_agent=f"{action['machine_id']}-AGENT",
            message_type="TASK_DISPATCH",
            status="DISPATCHING",
            summary=request["request_summary"],
            payload={
                "capability": action["capability"],
                "expected_metrics": action.get("expected_metrics") or {},
            },
        )
        result = self.manager.dispatch_task(request)
        execution = result.get("task") or {}
        state = self._task_state(execution) if execution else result.get("status", "UNKNOWN")
        if execution.get("reconciliation_required") or state == "RESULT_UNKNOWN":
            state = "RECONCILIATION_REQUIRED"
        return {
            "success": bool(result.get("success")) and state != "RECONCILIATION_REQUIRED",
            "status": state,
            "error_code": execution.get("error_code") or "",
            "message": execution.get("message") or result.get("message") or "",
        }

    def _record_action_outcome(
        self, operation_task_id: str, action: dict[str, Any], outcome: dict[str, Any]
    ) -> None:
        def update(row: dict[str, Any]) -> dict[str, Any]:
            plan = dict(row.get("plan") or {})
            failures = [
                item
                for item in plan.get("dispatch_failures") or []
                if item.get("action_id") != action["action_id"]
            ]
            if not outcome["success"]:
                failures.append(
                    {
                        **outcome,
                        "action_id": action["action_id"],
                        "machine_id": action["machine_id"],
                        "account_slot": action["account_slot"],
                        "recorded_at": utc_now(),
                    }
                )
            return {**row, "plan": {**plan, "dispatch_failures": failures}}

        self.manager.store.update("operation_tasks", operation_task_id, update)
        if not outcome["success"]:
            self._message(
                operation_task_id,
                from_agent=self.agent_id,
                to_agent="WEBUI",
                message_type="TASK_DISPATCH_FAILED",
                status=outcome["status"],
                summary=outcome.get("message") or outcome["status"],
                payload={**outcome, "action_id": action["action_id"]},
            )

    def _build_plan(self, task: dict[str, Any]) -> dict[str, Any]:
        goal = task.get("goal_snapshot") or {}
        metrics = {
            str(name): int(value)
            for name, value in (goal.get("target_metrics") or {}).items()
            if int(value) > 0
        }
        supported = {
            "browse",
            "like",
            "comment",
            "reply",
            "publish",
            "friend_request",
            "new_followers",
            "received_likes",
            "received_comments",
            "received_private_messages",
        }
        unsupported = sorted(name for name in metrics if name not in supported)
        targets = list(task.get("targets") or [])
        text = " ".join(
            str(task.get(name) or "") for name in ("title", "instructions", "completion_criteria")
        )
        video_only = "视频" in text
        actions: list[dict[str, Any]] = []
        for index, target in enumerate(targets):
            browse_count = _allocated_count(metrics.get("browse", 0), index, len(targets))
            like_count = _allocated_count(metrics.get("like", 0), index, len(targets))
            if browse_count or like_count:
                effective_browse = max(browse_count, like_count)
                actions.append(
                    {
                        "action_id": f"engagement-{index + 1}",
                        "machine_id": str(target["machine_id"]).upper(),
                        "account_slot": str(target["account_slot"]),
                        "capability": "home-engagement",
                        "expected_metrics": {"browse": browse_count, "like": like_count},
                        "parameters": {
                            "browse_count": effective_browse,
                            "like_count": like_count,
                            "video_only": video_only,
                            "duration_minutes": max(
                                10,
                                math.ceil((effective_browse * 60 + like_count * 180) / 60),
                            ),
                            "min_read_seconds": 15,
                            "max_read_seconds": 45,
                        },
                        "write_intent": like_count > 0,
                    }
                )
            comment_count = _allocated_count(metrics.get("comment", 0), index, len(targets))
            if comment_count:
                actions.append(
                    {
                        "action_id": f"comment-{index + 1}",
                        "machine_id": str(target["machine_id"]).upper(),
                        "account_slot": str(target["account_slot"]),
                        "capability": "random-comment",
                        "expected_metrics": {"comment": comment_count},
                        "parameters": {
                            "count": comment_count,
                            "candidate_pool_size": max(10, comment_count * 5),
                            "collection_minutes": 2,
                            "style": "natural",
                            "direct_send_authorized": True,
                        },
                        "write_intent": True,
                    }
                )
            inputs = (task.get("action_inputs") or {}).get(
                f"{target['machine_id']}:{target['account_slot']}", {}
            )
            for metric, capability, required in (
                ("reply", "reply-comment", ("content",)),
                ("friend_request", "follow-user", ("user_id", "xsec_token")),
                ("publish", "fill-publish", ("title", "content", "assets")),
            ):
                count = _allocated_count(metrics.get(metric, 0), index, len(targets))
                entries = inputs.get(metric) or []
                if not isinstance(entries, list) or len(entries) < count:
                    if count:
                        unsupported.append(
                            f"{metric} 缺少 {target['account_slot']} 的目标或素材，共需 {count} 项"
                        )
                    continue
                for sequence, params in enumerate(entries[:count]):
                    if not isinstance(params, dict) or any(not params.get(k) for k in required):
                        unsupported.append(
                            f"{metric} 第 {sequence + 1} 项缺少 {', '.join(required)}"
                        )
                        continue
                    if metric == "reply" and not (
                        (
                            params.get("feed_id")
                            and (params.get("comment_id") or params.get("user_id"))
                        )
                        or (params.get("notification_id") and params.get("original_content"))
                    ):
                        unsupported.append("reply 缺少目标笔记评论或通知上下文")
                        continue
                    action_capability = (
                        "fill-publish-video"
                        if metric == "publish" and params.get("kind") == "video"
                        else capability
                    )
                    parameters = dict(params)
                    if metric == "publish":
                        parameters = {"preview": {"kind": "image", **params}}
                    actions.append(
                        {
                            "action_id": f"{metric}-{index + 1}-{sequence + 1}",
                            **target,
                            "capability": action_capability,
                            "expected_metrics": {metric: 1},
                            "parameters": parameters,
                            "write_intent": True,
                        }
                    )
            growth = {
                "new_followers",
                "received_likes",
                "received_comments",
                "received_private_messages",
            }
            if task.get("plan_date") or growth.intersection(metrics):
                for capability in (
                    "collect-note-comments",
                    "collect-private-messages",
                    "collect-operations-metrics",
                ):
                    actions.append(
                        {
                            "action_id": f"{capability}-{index + 1}",
                            **target,
                            "capability": capability,
                            "parameters": {},
                            "expected_metrics": {},
                            "write_intent": False,
                        }
                    )
        summary = (
            "；".join(f"{item['machine_id']} 执行 {item['capability']}" for item in actions)
            or "当前目标没有需要执行的正数指标"
        )
        if not actions and not unsupported:
            unsupported = ["没有可执行指标"]
        return {
            "summary": summary,
            "planned_actions": actions,
            "unsupported_metrics": unsupported,
        }

    def _task_request(self, task: dict[str, Any], action: dict[str, Any]) -> dict[str, Any]:
        operation_task_id = str(task["operation_task_id"])
        capability = str(action["capability"])
        machine_id = str(action["machine_id"]).upper()
        task_id = self._action_task_id(task, action)
        expected = action.get("expected_metrics") or {}
        metric_text = "、".join(f"{name} {count}" for name, count in expected.items())
        return {
            "task_id": task_id,
            "machine_id": machine_id,
            "account_slot": str(action["account_slot"]),
            "capability": capability,
            "platform": next(
                (
                    a.get("platform", "xiaohongshu")
                    for a in self.manager.list_accounts()["accounts"]
                    if a["machine_id"] == machine_id and a["account_slot"] == action["account_slot"]
                ),
                "xiaohongshu",
            ),
            "request_summary": f"{machine_id} 执行 {metric_text}",
            "source_type": "manager-agent",
            "target_type": "operation_goal",
            "target_id": operation_task_id,
            "operation_task_id": operation_task_id,
            "goal_id": str(task.get("goal_id") or ""),
            "parameters": dict(action.get("parameters") or {}),
            "authorization": {
                "authorization_id": f"operation-task:{operation_task_id}",
                "scope": metric_text,
                "authorized_by": str(task.get("requested_by") or "WebUI用户"),
            },
            "write_intent": bool(action.get("write_intent")),
            "timeout_seconds": (
                float((action.get("parameters") or {}).get("duration_minutes", 10)) * 60 + 120
                if capability == "home-engagement"
                else 300.0
            ),
        }

    def _find_execution_task(self, task_id: str) -> dict[str, Any] | None:
        return next(
            (
                item
                for item in self.manager.list_tasks().get("tasks") or []
                if item.get("task_id") == task_id
            ),
            None,
        )

    def _linked_execution_tasks(self, operation_task_id: str) -> list[dict[str, Any]]:
        return [
            item
            for item in self.manager.list_tasks().get("tasks") or []
            if str((item.get("request") or {}).get("operation_task_id") or "") == operation_task_id
        ]

    @staticmethod
    def _task_state(task: dict[str, Any]) -> str:
        if task.get("error_code") == "PUBLISH_CONFIRMATION_REQUIRED":
            return "WAITING_APPROVAL"
        return str(
            task.get("business_state")
            or task.get("dispatch_state")
            or task.get("state")
            or "UNKNOWN"
        ).upper()

    def _message(
        self,
        operation_task_id: str,
        *,
        from_agent: str,
        to_agent: str,
        message_type: str,
        status: str,
        summary: str,
        task_id: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            self.manager.record_agent_message(
                {
                    "operation_task_id": operation_task_id,
                    "task_id": task_id,
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "message_type": message_type,
                    "status": status,
                    "summary": summary,
                    "payload": dict(payload or {}),
                }
            )
        except ServiceError:
            return
