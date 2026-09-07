"""Detached employee Agent runner for one accepted manager task."""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from .manager_worker_service import ManagerWorkerService
    from .run_lock import RunLock
except ImportError:
    from manager_worker_service import ManagerWorkerService
    from run_lock import RunLock


def run(state_root: str, task_id: str, remote_execution_id: str) -> int:
    service = ManagerWorkerService(root=state_root)
    lock = RunLock(str(Path(service.root) / "employee-agent.lock"))
    if not lock.acquire(timeout=86400):
        return 2
    try:
        service.send_task_progress(
            task_id,
            remote_execution_id=remote_execution_id,
            stage="starting",
            summary="员工Agent开始执行任务",
        )
        service.run_task(task_id, remote_execution_id=remote_execution_id)
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
    return parser


if __name__ == "__main__":
    arguments = build_parser().parse_args()
    raise SystemExit(run(arguments.state_root, arguments.task_id, arguments.remote_execution_id))
