from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from scripts.employee_reply_schedule import run_check
from scripts.manager_worker_service import ManagerWorkerService


def setup_worker(tmp_path):
    worker = ManagerWorkerService(tmp_path / "worker", account_summary_reader=lambda: [])
    worker.configure(
        machine_id="PC057",
        role="executor",
        project_version="0.1.0",
        task_inbox_root=str(tmp_path / "inbox"),
        task_outbox_root=str(tmp_path / "outbox"),
    )
    config = {
        "enabled": True,
        "interval_minutes": 20,
        "task": {
            "machine_id": "PC057",
            "account_slot": "ceshi3",
            "platform": "rednote",
            "allowed_versions": ["0.1.0"],
            "capability": "reply-inbox",
            "parameters": {},
        },
    }
    return worker, config


def test_busy_check_writes_outbox_and_deduplicates_period(tmp_path):
    worker, config = setup_worker(tmp_path)
    worker.accept_task(
        {
            "task_id": "busy",
            "dispatch_id": "busy",
            "target_machine_id": "PC057",
            "allowed_versions": ["0.1.0"],
            "task": {"account_slot": "ceshi3", "capability": "list-feeds"},
        }
    )
    now = datetime(2026, 9, 8, 9, 40, tzinfo=UTC)
    result = run_check(config, worker, now=now)
    assert result["status"] == "DEFERRED_ACCOUNT_BUSY"
    snapshot = worker.dashboard_snapshot()
    assert sum(item["record_type"] == "result" for item in snapshot["outbox_records"]) == 1
    execution = next(
        item for item in snapshot["executions"] if item["task_id"] == result["task_id"]
    )
    assert execution["business_result"]["inbox_checked"] is False
    assert run_check(config, worker, now=now + timedelta(minutes=5))["status"] == "ALREADY_CHECKED"
    assert (
        run_check(config, worker, now=now + timedelta(minutes=20))["task_id"] != result["task_id"]
    )


def test_idle_check_queues_employee_task(tmp_path, monkeypatch):
    worker, config = setup_worker(tmp_path)
    monkeypatch.setattr(worker, "wake_task", lambda task_id: {"task_id": task_id})
    result = run_check(config, worker, now=datetime(2026, 9, 8, 9, 40, tzinfo=UTC))
    envelope = json.loads(
        (tmp_path / "inbox" / "pending" / f"{result['task_id']}.json").read_text()
    )
    assert envelope["task"]["source_type"] == "executor"
    assert envelope["task"]["platform"] == "rednote"
    assert envelope["task"]["parameters"]["interval_minutes"] == 20
    assert run_check({**config, "enabled": False}, worker)["status"] == "DISABLED"
