from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from employee_codex_runner import EmployeeCodexRunner
from employee_task_cli import invoke, main, read_object, stage_bug, write_object


def task_root(tmp_path: Path) -> Path:
    write_object(tmp_path / "task.json", {
        "task_id": "latency", "account_slot": "alpha", "remote_execution_id": "run1",
        "parameters": {"count": 1, "memory_context": {"governance": ["rule"]}},
        "authorization": {"authorization_id": "approved"},
    })
    return tmp_path


@pytest.mark.parametrize("args", [["--help"], ["reply-inbox", "collect", "--help"]])
def test_help_uses_parser_without_business_call(tmp_path: Path, args: list[str]) -> None:
    root = task_root(tmp_path)
    request = root / "help.json"
    write_object(request, {"args": args})
    assert invoke(root, request) == 0
    assert "usage:" in request.with_suffix(".output.json").read_text(encoding="utf-8")
    assert read_object(request.with_suffix(".execution.json"))["elapsed_seconds"] >= 0


def test_rejected_arguments_have_execution_and_output(tmp_path: Path) -> None:
    root = task_root(tmp_path)
    request = root / "invalid.json"
    write_object(request, {"args": ["--account=other", "post-comment"]})
    assert invoke(root, request, lambda: pytest.fail("must not execute")) == 2
    assert read_object(request.with_suffix(".output.json"))["success"] is False
    assert read_object(request.with_suffix(".execution.json"))["exit_code"] == 2


def test_claim_retains_memory_and_exposes_authorization(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    root = task_root(tmp_path)
    monkeypatch.setattr(sys, "argv", ["entry", "--run-root", str(root), "claim"])
    main()
    brief = json.loads(capsys.readouterr().out)
    assert brief["authorization"]["authorization_id"] == "approved"
    assert "memory_context" not in brief["parameters"]
    assert read_object(Path(brief["memory_context_file"])) == {"governance": ["rule"]}
    assert "memory_context" in read_object(root / "task.json")["parameters"]


def test_runner_publishes_staged_bug_once_to_configured_outbox(tmp_path: Path) -> None:
    from manager_worker_service import ManagerWorkerService

    root = task_root(tmp_path)
    request = root / "bug.json"
    write_object(request, {"summary": "页面等待失败", "error_code": "PAGE_LOAD_TIMEOUT"})
    assert stage_bug(root, request)["state"] == "QUEUED"
    worker = ManagerWorkerService(tmp_path / "worker", account_summary_reader=lambda: [])
    outbox = tmp_path / "separate-outbox"
    worker.configure(machine_id="PC057", task_outbox_root=str(outbox))
    runner = EmployeeCodexRunner(tmp_path / "worker",
                                 on_bug=lambda payload: worker.debug_action("report", payload))
    runner._publish_bugs(root)
    runner._publish_bugs(root)
    assert len(list((outbox / "incidents").glob("*.json"))) == 1
    assert (root / "pending-bugs/bug.receipt").exists()


def test_bug_publish_failure_keeps_report_for_collection(tmp_path: Path) -> None:
    root = task_root(tmp_path)
    write_object(root / "bug.json", {"summary": "页面等待失败"})
    stage_bug(root, root / "bug.json")

    def denied(payload: dict) -> dict:
        raise PermissionError("outbox unavailable")

    EmployeeCodexRunner(tmp_path, on_bug=denied)._publish_bugs(root)
    assert (root / "pending-bugs/bug.json").exists()
    assert not (root / "pending-bugs/bug.receipt").exists()
    assert "outbox unavailable" in read_object(root / "pending-bugs/bug.error")["error"]


def test_bridge_trace_records_one_send_and_no_payload(tmp_path: Path, monkeypatch) -> None:
    from xhs.bridge import BridgePage

    trace = tmp_path / "bridge.jsonl"
    monkeypatch.setenv("XHS_EXECUTION_TRACE_FILE", str(trace))
    calls = []

    def request(self, method: str, params: dict) -> None:
        calls.append(method)
        raise TimeoutError("unknown")

    monkeypatch.setattr(BridgePage, "_request", request)
    with pytest.raises(TimeoutError):
        BridgePage()._call("click_element", {"secret": "private text"})
    assert calls == ["click_element"]
    assert json.loads(trace.read_text())["status"] == "error"
    assert "private text" not in trace.read_text()


def test_navigation_behavior() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("本机未安装 Node.js；扩展行为回归由经理构建环境执行")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("test_navigation_readiness.cjs"))],
        capture_output=True, text=True, encoding="utf-8", env={**os.environ},
    )
    assert result.returncode == 0, result.stdout + result.stderr
