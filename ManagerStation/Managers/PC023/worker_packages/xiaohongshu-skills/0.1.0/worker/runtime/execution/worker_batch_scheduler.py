"""Durable employee queue scheduling, independent of manager availability."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

FINAL = {"SUCCESS", "PARTIAL_SUCCESS", "FAILED", "BLOCKED", "CANCELLED", "RESULT_UNKNOWN"}


def queue_status(state: dict, execution: dict, now: datetime | None = None) -> tuple[str, str]:
    now = now or datetime.now(UTC)
    params = execution["task"].get("parameters") or {}
    if (state.get("machine") or {}).get("paused", False):
        return "WAITING", "员工机已暂停"
    expires = params.get("expires_at")
    if expires and now >= datetime.fromisoformat(expires):
        return "EXPIRED", "批次执行窗口已结束"
    scheduled = params.get("scheduled_at")
    if scheduled and now < datetime.fromisoformat(scheduled):
        return "WAITING", f"等待计划时间 {scheduled}"
    by_task = {e["task_id"]: e for e in state["executions"].values()}

    def effective_execution(row: dict | None) -> dict | None:
        if not row or row["state"] != "PAUSED":
            return row
        return next(
            (
                candidate
                for candidate in by_task.values()
                if candidate["state"] == "SUCCESS"
                and (candidate["task"].get("parameters") or {}).get("advances_task_id")
                == row["task_id"]
                and candidate["task"].get("account_slot") == row["task"].get("account_slot")
            ),
            row,
        )

    predecessor = params.get("machine_predecessor_task_id")
    if predecessor:
        previous = effective_execution(by_task.get(predecessor))
        if not previous or previous["state"] not in FINAL:
            return "WAITING", "等待本机上一任务结束"
        if previous.get("finished_at") and now < datetime.fromisoformat(
            previous["finished_at"]
        ) + timedelta(seconds=params.get("batch_interval_seconds", 300)):
            return "WAITING", "等待本机任务间隔"
    for key in params.get("depends_on_task_ids", []):
        previous = effective_execution(by_task.get(key))
        if not previous or previous["state"] not in FINAL:
            return "WAITING", "等待上一批完成"
        if previous["state"] not in {"SUCCESS", "PARTIAL_SUCCESS"}:
            return "BLOCKED", "前批存在异常或结果待核对，后续批次暂停"
        finished = previous.get("finished_at")
        if finished and now < datetime.fromisoformat(finished) + timedelta(
            seconds=params.get("batch_interval_seconds", 0)
        ):
            return "WAITING", "等待批间隔"
    slot = execution["task"].get("account_slot")
    order = tuple(
        datetime.fromisoformat(value)
        for value in (
            scheduled or execution["accepted_at"],
            execution["accepted_at"],
        )
    )
    for other in state["executions"].values():
        if other["remote_execution_id"] == execution["remote_execution_id"]:
            continue
        if other["state"] in FINAL or other["state"] == "PAUSED":
            continue
        if effective_execution(other) is not other:
            continue
        if other["task"].get("account_slot") != slot and not params.get("machine_serial"):
            continue
        other_params = other["task"].get("parameters") or {}
        # Delivery order must not make a dependent batch block its own predecessor.
        pending = [other["task_id"]]
        seen = set()
        depends_on_current = False
        while pending:
            task_id = pending.pop()
            if task_id in seen:
                continue
            seen.add(task_id)
            row = by_task.get(task_id)
            if not row:
                continue
            links = row["task"].get("parameters") or {}
            dependencies = list(links.get("depends_on_task_ids") or [])
            if links.get("machine_predecessor_task_id"):
                dependencies.append(links["machine_predecessor_task_id"])
            if execution["task_id"] in dependencies:
                depends_on_current = True
                break
            pending.extend(dependencies)
        if depends_on_current and other["state"] != "RUNNING":
            continue
        other_order = tuple(
            datetime.fromisoformat(value)
            for value in (
                other_params.get("scheduled_at") or other["accepted_at"],
                other["accepted_at"],
            )
        )
        if other["state"] == "RUNNING" or other_order < order:
            return "WAITING", "等待同账号任务完成"
    return "READY", "计划已到点，等待员工 Agent 领取"


class WorkerBatchScheduler:
    def __init__(self, service: Any, interval_seconds: float = 10) -> None:
        self.service = service
        self.interval_seconds = interval_seconds
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def tick(self) -> None:
        from pathlib import Path

        state = self.service._store.snapshot()
        if state["machine"]["paused"]:
            return
        pending = Path(state["config"]["task_inbox_root"]) / "pending"
        # Import delivered files even when the SSH wake acknowledgement was lost.
        for path in sorted(pending.glob("*.json"))[:20]:
            try:
                self.service.wake_task(path.stem, file_name=path.name)
            except Exception:
                logging.exception("员工任务领取失败: %s", path.name)
        self.service.reconcile_agent_exits()
        state = self.service._store.snapshot()
        for execution in state["executions"].values():
            if execution["state"] == "ACCEPTED":
                if queue_status(state, execution)[0] == "WAITING":
                    continue
                try:
                    self.service.start_task(remote_execution_id=execution["remote_execution_id"])
                except Exception:
                    logging.exception("员工任务启动失败: %s", execution["task_id"])

    def start(self) -> None:
        def loop() -> None:
            while not self.stop_event.is_set():
                try:
                    self.tick()
                except Exception:
                    logging.exception("员工本地调度失败，下一轮继续")
                self.stop_event.wait(self.interval_seconds)

        self.thread = threading.Thread(target=loop, name="worker-batch-scheduler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
