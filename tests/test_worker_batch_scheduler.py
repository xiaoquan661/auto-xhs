from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from manager_worker_service import ManagerWorkerService
from run_lock import RunLock
from worker_batch_scheduler import WorkerBatchScheduler, queue_status

from tests.test_manager_worker import make_service, task_request


@pytest.mark.parametrize("exit_detected", [False, True])
def test_completed_work_survives_state_commit_failure_and_restart(
    tmp_path, monkeypatch, exit_detected
):
    calls = []
    service = make_service(tmp_path, executor=lambda task: calls.append(task) or {
        "state": "SUCCESS", "business_result": {"count": 1}, "item_results": [],
    })
    service.accept_task(task_request())
    mutate = service._store.mutate

    def fail_final_commit(callback):
        if callback.__name__ == "finalize":
            raise PermissionError("state file shared by reader")
        return mutate(callback)

    monkeypatch.setattr(service._store, "mutate", fail_final_commit)
    with pytest.raises(PermissionError):
        service.run_task("task-1")
    assert len(calls) == 1
    assert list((service.root / "pending-outcomes").glob("*.json"))
    restarted = ManagerWorkerService(tmp_path)
    if exit_detected:
        def mark_exit(state):
            row = next(iter(state["executions"].values()))
            row.update(state="RESULT_UNKNOWN", error_code="AGENT_PROCESS_EXITED")
        restarted._store.mutate(mark_exit)
    restarted.reconcile_agent_exits()
    assert restarted.task_status("task-1")["execution"]["state"] == "SUCCESS"
    assert not list((service.root / "pending-outcomes").glob("*.json"))
    assert list((service.root / "pending-outcomes").glob("*.done"))
    before = restarted._store.snapshot()
    restarted.reconcile_agent_exits()
    assert restarted._store.snapshot()["next_sequence"] == before["next_sequence"]
    assert len(calls) == 1


def test_outbox_failure_retries_receipt_without_repeating_business(tmp_path, monkeypatch):
    service = make_service(tmp_path, executor=lambda task: {"state": "SUCCESS"})
    service.accept_task(task_request())
    service.run_task("task-1")
    push = service.push_task_result
    monkeypatch.setattr(service, "push_task_result", lambda **kw: (_ for _ in ()).throw(
        OSError("outbox temporarily unavailable")
    ))
    service.recover_pending_outcomes()
    assert list((service.root / "pending-outcomes").glob("*.json"))
    monkeypatch.setattr(service, "push_task_result", push)
    service.recover_pending_outcomes()
    assert not list((service.root / "pending-outcomes").glob("*.json"))
    state = service._store.snapshot()
    assert len(state["results"]) == 1
    assert len([r for r in state["outbox_records"].values()
                if r["record_type"] == "result"]) == 1


def test_queue_snapshot_reads_committed_state_while_writer_holds_lock(tmp_path):
    service = make_service(tmp_path)
    service.accept_task(task_request())
    with RunLock(str(service._store.lock_path)):
        snapshot = service._store.snapshot()
        assert len(snapshot["executions"]) == 1
        snapshot["executions"].clear()
        assert len(service._store.snapshot()["executions"]) == 1


def test_worker_commit_retries_windows_reader_sharing_conflict(tmp_path, monkeypatch):
    import os

    service = make_service(tmp_path)
    replace = os.replace
    attempts = []

    def shared_reader(source, target):
        attempts.append(target)
        if len(attempts) < 3:
            raise PermissionError("Windows reader still has the state file open")
        return replace(source, target)

    monkeypatch.setattr("manager_worker_service.os.replace", shared_reader)
    service.accept_task(task_request())
    assert len(attempts) >= 3
    assert len(service._store.snapshot()["executions"]) == 1


def test_future_delivery_restart_due_and_completion_spacing(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    starts = []
    service = make_service(
        tmp_path, process_starter=lambda *a, **k: starts.append(a) or SimpleNamespace(pid=12345)
    )
    first = task_request()
    first["task"]["parameters"].update(scheduled_at=(now + timedelta(hours=1)).isoformat())
    service.accept_task(first)
    service.start_task("task-1")
    assert not starts
    # A new service instance simulates restart; the accepted timetable remains on disk.
    restarted = ManagerWorkerService(tmp_path, process_starter=service._process_starter)
    state = restarted._store.snapshot()
    execution = next(iter(state["executions"].values()))
    assert queue_status(state, execution, now)[0] == "WAITING"
    assert queue_status(state, execution, now + timedelta(hours=1))[0] == "READY"
    second = task_request(task_id="task-2", dispatch_id="dispatch-2")
    second["task"]["parameters"].update(depends_on_task_ids=["task-1"], batch_interval_seconds=300)
    restarted.accept_task(second)
    state = restarted._store.snapshot()
    rows = {e["task_id"]: e for e in state["executions"].values()}
    rows["task-1"].update(state="SUCCESS", finished_at=now.isoformat())
    assert queue_status(state, rows["task-2"], now + timedelta(seconds=299))[0] == "WAITING"
    assert queue_status(state, rows["task-2"], now + timedelta(seconds=300))[0] == "READY"
    rows["task-1"]["state"] = "RESULT_UNKNOWN"
    assert queue_status(state, rows["task-2"], now + timedelta(hours=1))[0] == "BLOCKED"


def test_dead_unclaimed_runner_recovers_once_and_live_runner_is_preserved(tmp_path, monkeypatch):
    starts = []
    service = make_service(
        tmp_path, process_starter=lambda *a, **k: starts.append(a) or SimpleNamespace(pid=12345)
    )
    service.accept_task(task_request())
    service.start_task("task-1")
    ident = next(iter(service._store.snapshot()["executions"]))

    def age(state):
        state["executions"][ident]["runner_started_at"] = (
            datetime.now(UTC) - timedelta(minutes=2)
        ).isoformat()

    service._store.mutate(age)
    monkeypatch.setattr("manager_worker_service.process_is_running", lambda pid: False)
    WorkerBatchScheduler(service).tick()
    WorkerBatchScheduler(service).tick()
    assert len(starts) == 2
    service._store.mutate(age)
    monkeypatch.setattr("manager_worker_service.process_is_running", lambda pid: True)
    WorkerBatchScheduler(service).tick()
    assert len(starts) == 2


def test_claimed_dead_runner_is_not_replayed(tmp_path, monkeypatch):
    starts = []
    service = make_service(
        tmp_path, process_starter=lambda *a, **k: starts.append(a) or SimpleNamespace(pid=12345)
    )
    service.accept_task(task_request())
    service.start_task("task-1")
    ident = next(iter(service._store.snapshot()["executions"]))
    root = Path(service.root) / "agent-runs" / ident
    root.mkdir(parents=True)
    (root / "claimed.json").write_text("{}", encoding="utf-8")

    def age(state):
        state["executions"][ident]["runner_started_at"] = "2020-01-01T00:00:00+00:00"

    service._store.mutate(age)
    monkeypatch.setattr("manager_worker_service.process_is_running", lambda pid: False)
    WorkerBatchScheduler(service).tick()
    assert len(starts) == 1
    assert service.task_status("task-1")["execution"]["state"] == "RESULT_UNKNOWN"


@pytest.mark.parametrize("explicit_predecessor", [False, True])
def test_paused_task_reserves_only_explicit_successors(tmp_path, explicit_predecessor):
    service = make_service(tmp_path)
    first = task_request()
    service.accept_task(first)
    service.pause_task("task-1")
    second = task_request(task_id="task-2", dispatch_id="dispatch-2")
    second["task"]["parameters"].update(machine_serial=True)
    if explicit_predecessor:
        second["task"]["parameters"]["machine_predecessor_task_id"] = "task-1"
    service.accept_task(second)
    state = service._store.snapshot()
    row = next(row for row in state["executions"].values() if row["task_id"] == "task-2")
    assert queue_status(state, row)[0] == ("WAITING" if explicit_predecessor else "READY")


def test_machine_serial_waits_other_account_and_continues_after_blocked(tmp_path):
    service = make_service(tmp_path)
    service.accept_task(task_request())
    second = task_request(task_id="task-2", dispatch_id="dispatch-2")
    second["task"]["account_slot"] = "other"
    second["task"]["parameters"].update(machine_serial=True,
        machine_predecessor_task_id="task-1", batch_interval_seconds=300)
    service.accept_task(second)
    state = service._store.snapshot()
    rows = {e["task_id"]: e for e in state["executions"].values()}
    now = datetime.now(UTC)
    rows["task-1"]["state"] = "RUNNING"
    assert queue_status(state, rows["task-2"], now)[0] == "WAITING"
    rows["task-1"].update(state="BLOCKED", finished_at=now.isoformat())
    assert queue_status(state, rows["task-2"], now + timedelta(seconds=299))[0] == "WAITING"
    assert queue_status(state, rows["task-2"], now + timedelta(seconds=300))[0] == "READY"


def test_advanced_publish_releases_paused_batch_successor(tmp_path):
    service = make_service(tmp_path)
    original = task_request()
    original["task"]["parameters"]["machine_serial"] = True
    service.accept_task(original)
    advanced = task_request(task_id="task-early", dispatch_id="dispatch-early")
    advanced["task"]["parameters"].update(
        machine_serial=True, advances_task_id="task-1"
    )
    service.accept_task(advanced)
    successor = task_request(task_id="task-2", dispatch_id="dispatch-2")
    successor["task"]["parameters"].update(
        machine_serial=True, machine_predecessor_task_id="task-1",
        batch_interval_seconds=300,
    )
    service.accept_task(successor)
    state = service._store.snapshot()
    rows = {e["task_id"]: e for e in state["executions"].values()}
    now = datetime.now(UTC)
    rows["task-1"]["state"] = "PAUSED"
    rows["task-early"].update(state="SUCCESS", finished_at=now.isoformat())
    assert queue_status(state, rows["task-2"], now + timedelta(seconds=299))[0] == "WAITING"
    assert queue_status(state, rows["task-2"], now + timedelta(seconds=300))[0] == "READY"
    rows["task-early"]["state"] = "FAILED"
    assert queue_status(state, rows["task-2"], now + timedelta(hours=1))[0] == "WAITING"


def test_queue_orders_mixed_offsets_by_instant():
    now = datetime(2026, 9, 23, 10, tzinfo=UTC)
    def row(key, scheduled):
        return {"task_id": key, "remote_execution_id": key, "state": "ACCEPTED",
                "accepted_at": "2026-09-23T08:00:00+00:00",
                "task": {"account_slot": "same", "parameters": {"scheduled_at": scheduled}}}
    first = row("first", "2026-09-23T17:07:50+08:00")
    second = row("second", "2026-09-23T09:07:51+00:00")
    state = {"machine": {}, "executions": {"first": first, "second": second}}
    assert queue_status(state, first, now)[0] == "READY"
    assert queue_status(state, second, now)[0] == "WAITING"

def test_dependent_delivered_first_does_not_block_predecessor(tmp_path):
    service = make_service(tmp_path)
    second = task_request(task_id='task-2', dispatch_id='dispatch-2')
    second['task']['parameters'].update(machine_serial=True, machine_predecessor_task_id='task-1')
    service.accept_task(second)
    first = task_request()
    first['task']['parameters']['machine_serial'] = True
    service.accept_task(first)
    state = service._store.snapshot()
    rows = {e['task_id']: e for e in state['executions'].values()}
    assert queue_status(state, rows['task-1'])[0] == 'READY'
    assert queue_status(state, rows['task-2'])[0] == 'WAITING'
    rows['task-2']['state'] = 'RUNNING'
    assert queue_status(state, rows['task-1'])[0] == 'WAITING'
