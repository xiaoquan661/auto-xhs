from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import account_manager
import daily_passive_collection
import direct_creator_metrics
import product_store
import pytest
from direct_creator_metrics import DirectCreatorMetricsRunner, is_direct_creator_task
from manager_worker import dispatch_request

from tests.test_manager_worker import make_service


def test_local_publish_scope_runs_fixed_cli_with_receipt_ids(tmp_path: Path) -> None:
    config = tmp_path / "daily-creator" / "config.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps({"accounts": {"3-3": {"local_published_only": True}}}), encoding="utf-8"
    )
    executions = {
        "first": {
            "state": "SUCCESS",
            "task": {"account_slot": "3-3", "capability": "publish"},
            "business_result": {"published": 1, "note_id": "note-1"},
        },
        "second": {
            "state": "SUCCESS",
            "task": {"account_slot": "3-3", "capability": "publish"},
            "business_result": {"published": 1, "note_id": "note-2"},
        },
    }
    seen = []

    def command_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        result = {
            "task": {"task_id": "local-task", "state": "SUCCESS"},
            "result": {
                "source": "creator_manager",
                "captured_at": "2026-09-29T06:00:00Z",
                "notes": [
                    {"note_id": "note-1", "metrics": {"views": 0}},
                    {"note_id": "note-2", "metrics": {"views": 5}},
                ],
                "missing_note_ids": [],
                "partial": False,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(result))

    task = {
        "account_slot": "3-3",
        "capability": "collect-operations-metrics",
        "parameters": {"source": "creator_manager", "max_notes": 100},
    }
    assert is_direct_creator_task(task)
    outcome = DirectCreatorMetricsRunner(
        tmp_path,
        worker_snapshot=lambda: {"executions": executions},
        command_runner=command_runner,
    )(task)
    assert outcome["state"] == "SUCCESS"
    assert outcome["business_result"]["inventory_complete"] is True
    assert outcome["business_result"]["requested_note_ids"] == ["note-1", "note-2"]
    assert outcome["business_result"]["notes"][0]["metrics"]["views"] == 0
    assert seen[0].count("--note-id") == 2
    assert Path(seen[0][0]).name.lower() == "python.exe"
    assert Path(seen[0][3]).name == "cli.py"


def test_empty_local_publish_scope_returns_confirmed_zero_without_cli(tmp_path: Path) -> None:
    runner = DirectCreatorMetricsRunner(
        tmp_path,
        worker_snapshot=lambda: {"executions": {}},
        command_runner=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("CLI called")),
    )
    outcome = runner(
        {
            "account_slot": "3-3",
            "capability": "collect-operations-metrics",
            "parameters": {"source": "creator_manager", "local_published_only": True},
        }
    )
    assert outcome["state"] == "SUCCESS"
    assert outcome["business_result"]["count"] == 0
    assert outcome["business_result"]["inventory_complete"] is True


def test_cli_partial_result_keeps_missing_note_ids(tmp_path: Path) -> None:
    def command_runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        value = {
            "task": {"state": "SUCCESS"},
            "result": {
                "source": "creator_manager",
                "notes": [],
                "missing_note_ids": ["note-1"],
                "partial": True,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(value))

    outcome = DirectCreatorMetricsRunner(
        tmp_path,
        worker_snapshot=lambda: {"executions": {}},
        command_runner=command_runner,
    )(
        {
            "account_slot": "3-3",
            "capability": "collect-operations-metrics",
            "parameters": {"source": "creator_manager", "note_ids": ["note-1"]},
        }
    )
    assert outcome["state"] == "PARTIAL_SUCCESS"
    assert outcome["business_result"]["missing_note_ids"] == ["note-1"]


@pytest.mark.parametrize("eligibility_status", ["READY", "DEFERRED"])
def test_interactive_worker_action_returns_direct_receipt(
    tmp_path: Path, monkeypatch, eligibility_status: str,
) -> None:
    worker = make_service(tmp_path / "worker")
    monkeypatch.setattr(account_manager, "list_accounts", lambda: [
        SimpleNamespace(name="alpha", platform="xiaohongshu")])
    monkeypatch.setattr(daily_passive_collection, "eligibility", lambda *args: eligibility_status)
    monkeypatch.setattr(product_store, "ProductStore", lambda: SimpleNamespace(
        snapshot=lambda: {"settings": {}}))
    monkeypatch.setattr(direct_creator_metrics, "DirectCreatorMetricsRunner",
                        lambda *args, **kwargs: lambda task: {
                            "state": "SUCCESS", "message": "本机 CLI 已采集 1 篇作品",
                            "business_result": {"source": "creator_manager", "notes": [
                                {"note_id": "note-1", "metrics": {"views": 0}}]},
                        })

    response = dispatch_request(worker, {"action": "collect-creator-now",
                                         "payload": {"account_slot": "alpha"}})

    assert response["status"] == "SUCCESS"
    assert response["result"]["source_type"] == "manager_direct"
    assert response["result"]["business_result"]["notes"][0]["metrics"]["views"] == 0
