from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.test_manager_worker import make_service, task_request


def running_worker(tmp_path: Path, execution_state: str = "RUNNING"):
    service = make_service(tmp_path)
    accepted = service.accept_task(task_request())
    execution_id = accepted["execution"]["remote_execution_id"]

    def start(state: dict) -> None:
        state["executions"][execution_id].update(
            state=execution_state,
            runner_pid=123,
            runner_started_at=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        )

    service._store.mutate(start)
    root = service.root / "agent-runs" / execution_id
    root.mkdir(parents=True)
    (root / "process.json").write_text(json.dumps({"pid": 456}), encoding="utf-8")
    return service


@pytest.mark.parametrize("execution_state", ["RUNNING", "RECONCILIATION_REQUIRED"])
def test_health_reconciles_exited_agent_once_and_publishes_receipt(
    tmp_path, monkeypatch, execution_state
) -> None:
    service = running_worker(tmp_path, execution_state)
    monkeypatch.setattr("manager_worker_service.process_is_running", lambda _: False)
    service.health()
    service.health()
    state = service._store.snapshot()
    execution = next(iter(state["executions"].values()))
    assert execution["state"] == "RESULT_UNKNOWN"
    assert execution["error_code"] == "AGENT_PROCESS_EXITED"
    assert execution["reconciliation_required"] is True
    assert len(state["results"]) == 1
    assert any(row["record_type"] == "result" for row in state["outbox_records"].values())


@pytest.mark.parametrize("live_pid", [123, 456])
def test_health_preserves_live_runner_or_child(tmp_path, monkeypatch, live_pid) -> None:
    service = running_worker(tmp_path)
    monkeypatch.setattr("manager_worker_service.process_is_running", lambda pid: pid == live_pid)
    service.health()
    state = service._store.snapshot()
    assert next(iter(state["executions"].values()))["state"] == "RUNNING"
    assert not state["results"]
