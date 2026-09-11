"""Detached employee Agent runner for one accepted manager task."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from urllib.parse import quote

try:
    from .employee_codex_runner import EmployeeCodexRunner
    from .manager_worker_service import TASK_FINAL_STATES, ManagerWorkerService
    from .run_lock import RunLock
except ImportError:
    from employee_codex_runner import EmployeeCodexRunner
    from manager_worker_service import TASK_FINAL_STATES, ManagerWorkerService
    from run_lock import RunLock


def queue_ready(state: dict, execution: dict) -> bool:
    """Keep acceptance order inside one account, without holding other accounts."""
    slot = execution["task"].get("account_slot")
    order = (execution["accepted_at"], execution["remote_execution_id"])
    for other in state["executions"].values():
        if other["task"].get("account_slot") != slot:
            continue
        if (other["accepted_at"], other["remote_execution_id"]) < order and (
            other["state"] not in TASK_FINAL_STATES
        ):
            return False
    dependencies = (execution["task"].get("parameters") or {}).get("depends_on_task_ids", [])
    by_task = {row["task_id"]: row for row in state["executions"].values()}
    return all(
        by_task.get(key, {}).get("state") in TASK_FINAL_STATES for key in dependencies
    )


def run(state_root: str, task_id: str, remote_execution_id: str) -> int:
    service = ManagerWorkerService(root=state_root)
    execution = service._store.snapshot()["executions"][remote_execution_id]
    slot = str(execution["task"].get("account_slot") or "default")
    lock = RunLock(str(Path(service.root) / "agent-locks" / (quote(slot, safe="") + ".lock")))
    while True:
        state = service._store.snapshot()
        execution = state["executions"][remote_execution_id]
        if state["machine"]["paused"] or execution["state"] != "ACCEPTED":
            return 0
        if queue_ready(state, execution) and lock.acquire(timeout=1):
            break
        time.sleep(2)
    try:
        service.send_task_progress(
            task_id,
            remote_execution_id=remote_execution_id,
            stage="starting",
            summary="员工Agent开始执行任务",
        )

        def claimed(receipt: dict) -> None:
            service.send_task_progress(
                task_id,
                remote_execution_id=remote_execution_id,
                stage="agent_claimed",
                summary="员工 Codex Agent 已读取并领取任务",
                details=receipt,
            )

        def should_stop() -> bool:
            execution = service.task_status(task_id, remote_execution_id=remote_execution_id)[
                "execution"
            ]
            return execution["state"] != "RUNNING"

        service.run_task(
            task_id,
            remote_execution_id=remote_execution_id,
            executor=(
                (lambda task: {"state": "BLOCKED", "message": "前置任务未成功完成，请核对前置回执"})
                if any(
                    row["state"] not in {"SUCCESS", "PARTIAL_SUCCESS"}
                    for row in state["executions"].values()
                    if row["task_id"] in (execution["task"].get("parameters") or {}).get(
                        "depends_on_task_ids", []
                    )
                )
                else EmployeeCodexRunner(
                    service.root, on_claim=claimed, should_stop=should_stop,
                    on_bug=lambda payload: service.debug_action("report", payload),
                )
            ),
        )
        service.record_outcome_progress(task_id, remote_execution_id=remote_execution_id)
        service.push_task_result(task_id, remote_execution_id=remote_execution_id)
        return 0
    finally:
        lock.release()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="执行一条员工Agent异步任务")
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--remote-execution-id", required=True)
    parser.add_argument("--startup-receipt")
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    if arguments.startup_receipt:
        receipt = Path(arguments.startup_receipt)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps({"pid": os.getpid()}, ensure_ascii=False), encoding="utf-8")
        temporary.replace(receipt)
    raise SystemExit(run(arguments.state_root, arguments.task_id, arguments.remote_execution_id))
