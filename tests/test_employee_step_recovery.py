from __future__ import annotations

import json
from pathlib import Path

from employee_recovery import can_resume, start_step
from employee_task_cli import invoke, write_object


def setup_task(root: Path, command: str) -> Path:
    write_object(root / "task.json", {"task_id": "one", "account_slot": "alpha"})
    request = root / "step.json"
    write_object(request, {"args": [command]})
    return request


def test_snapshot_transient_failure_recovers_once(tmp_path: Path) -> None:
    request = setup_task(tmp_path, "browser-snapshot")
    calls = []

    def cli() -> None:
        calls.append(True)
        if len(calls) == 1:
            print(json.dumps({"success": False, "error": "页面已变化"}))
            raise SystemExit(2)
        print(json.dumps({"success": True}))

    assert invoke(tmp_path, request, cli) == 0
    assert len(calls) == 2
    assert json.loads(request.with_suffix(".output.json").read_text()) == {"success": True}
    assert request.with_suffix(".attempt1.output.json").exists()


def test_successful_write_is_not_replayed(tmp_path: Path) -> None:
    request = setup_task(tmp_path, "post-comment")
    calls = []

    def cli() -> None:
        calls.append(True)
        print(json.dumps({"success": True, "platform_readback": True}))

    assert invoke(tmp_path, request, cli) == 0
    assert invoke(tmp_path, request, cli) == 0
    assert len(calls) == 1
    assert can_resume(tmp_path)


def test_unknown_write_stays_pending(tmp_path: Path) -> None:
    request = setup_task(tmp_path, "post-comment")
    calls = []

    def cli() -> None:
        calls.append(True)
        print(json.dumps({"success": False, "status": "RESULT_UNKNOWN"}))
        raise SystemExit(2)

    assert invoke(tmp_path, request, cli) == 2
    assert invoke(tmp_path, request, cli) == 2
    assert len(calls) == 1
    assert not can_resume(tmp_path)


def test_inflight_write_prevents_automatic_restart(tmp_path: Path) -> None:
    request = setup_task(tmp_path, "follow-user")
    start_step(tmp_path, request, ["follow-user"])
    assert not can_resume(tmp_path)
    start_step(tmp_path, request, ["browser-snapshot"])
    assert can_resume(tmp_path)


def test_runner_resumes_remaining_steps_once(tmp_path: Path, monkeypatch) -> None:
    from employee_codex_runner import EmployeeCodexRunner

    request = setup_task(tmp_path, "browser-snapshot")
    start_step(tmp_path, request, ["browser-snapshot"])
    seen = []

    def resumed(self, task):
        seen.append(task)
        return {"state": "SUCCESS"}

    monkeypatch.setattr(EmployeeCodexRunner, "__call__", resumed)
    runner = EmployeeCodexRunner(tmp_path)
    assert runner._resume_or_incomplete({"task_id": "one"}, "中断", tmp_path)["state"] == "SUCCESS"
    assert seen[0]["recovery_attempt"] == 1
    result = runner._resume_or_incomplete(
        {"task_id": "one", "recovery_attempt": 1}, "中断", tmp_path
    )
    assert result["business_result"]["step_checkpoint"]["step.json"]["state"] == "RUNNING"
    assert len(seen) == 1
