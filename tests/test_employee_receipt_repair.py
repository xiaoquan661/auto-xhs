from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import employee_codex_runner as module
import pytest
from employee_codex_runner import PROBE_CAPABILITY, EmployeeCodexRunner


def test_relative_input_resolves_in_task_directory(tmp_path, monkeypatch):
    import employee_task_cli

    (tmp_path / "task.json").write_text('{"task_id":"test","account_slot":"alpha"}')
    monkeypatch.setattr(sys, "argv", ["employee-task", "--run-root", str(tmp_path),
                                     "invoke", "--input-file", "command.json"])
    received = []
    def invoke(root, path):
        received.append(path)
        path.with_suffix(".output.json").write_text('{"success":true}', encoding="utf-8")
        return 0

    monkeypatch.setattr(employee_task_cli, "invoke", invoke)
    with pytest.raises(SystemExit) as outcome:
        employee_task_cli.main()
    assert outcome.value.code == 0
    assert received == [tmp_path / "command.json"]


@pytest.mark.parametrize("agent_exit", [0, 1])
@pytest.mark.parametrize("final_pointer", [True, False])
def test_startup_failure_preserves_receipt(tmp_path, monkeypatch, agent_exit, final_pointer):
    source = tmp_path / "fake.py"
    source.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "root=Path(sys.argv[1]);sys.stdin.read()\n"
        "result={'state':'FAILED','message':'领取命令解析失败',"
        "'business_result':{'business_executed':False,'completed_count':0},"
        "'item_results':[],'evidence_paths':[]}\n"
        "(root/'execution-result.json').write_text(json.dumps(result),encoding='utf-8')\n"
        + ("(root/'result.json').write_text(json.dumps({'result_file':'execution-result.json'}))\n"
           if final_pointer else "")
        + f"sys.exit({agent_exit})\n", encoding="utf-8",
    )
    original = subprocess.Popen

    def start(command, **kwargs):
        root = Path(command[command.index("-o") + 1]).parent
        return original([sys.executable, str(source), str(root)], **kwargs)

    monkeypatch.setattr(module, "find_codex", lambda: "fake-codex")
    monkeypatch.setattr(module.subprocess, "Popen", start)
    result = EmployeeCodexRunner(tmp_path / "state")({
        "task_id": "repair-probe", "remote_execution_id": "repair-probe",
        "account_slot": "test", "capability": PROBE_CAPABILITY,
        "write_intent": True, "timeout_seconds": 10,
    })
    assert result["state"] == "FAILED"
    assert result["message"] == "领取命令解析失败"
    assert result["business_result"]["completed_count"] == 0
    assert result["business_result"]["receipt_status"]["claimed"] is False


@pytest.mark.skipif(os.name != "nt", reason="Windows command entry")
def test_windows_entry_claim_and_finish(tmp_path):
    root = tmp_path / "任务 space"
    root.mkdir()
    (root / "task.json").write_text(json.dumps({
        "task_id": "local-probe", "account_slot": "fixture",
    }), encoding="utf-8")
    runner = EmployeeCodexRunner(tmp_path / "state")
    runner._write_entry(root)
    for action in ("claim", "finish"):
        if action == "finish":
            (root / "execution-result.json").write_text(json.dumps({
                "state": "SUCCESS", "message": "本地验证", "business_result": {},
                "item_results": [], "evidence_paths": [],
            }), encoding="utf-8")
        result = subprocess.run(
            f'cmd.exe /d /c "call task-entry.cmd {action}"', cwd=root,
            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    claim = json.loads((root / "claimed.json").read_text(encoding="utf-8"))
    assert claim["task_id"] == "local-probe"
