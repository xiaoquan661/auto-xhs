from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import employee_process
import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows 员工桌面启动")


def test_launcher_parses_in_windows_powershell() -> None:
    script = Path(employee_process.__file__).with_name("start-employee-desktop.ps1")
    command = (
        "$errors=$null;$tokens=$null;"
        "[Management.Automation.Language.Parser]::ParseFile('"
        + str(script).replace("'", "''")
        + "',[ref]$tokens,[ref]$errors) | Out-Null;"
        "if ($errors.Count) { $errors | ForEach-Object Message; exit 1 }"
    )
    encoded = base64.b64encode(command.encode("utf-16-le")).decode("ascii")
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-EncodedCommand", encoded],
        capture_output=True,
        timeout=15,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_desktop_launcher_returns_actual_runner_pid(tmp_path: Path, monkeypatch) -> None:
    requests = []

    def run(command, **kwargs):
        requests.append((command, json.loads(kwargs["input"])))
        return subprocess.CompletedProcess(command, 0, '{"success":true,"pid":2468}', "")

    monkeypatch.setattr(employee_process.subprocess, "run", run)
    process = employee_process.start_employee_process(
        [
            str(tmp_path / "python.exe"),
            "manager_worker_async.py",
            "--state-root",
            str(tmp_path),
            "--task-id",
            "task-1",
            "--remote-execution-id",
            "execution-1",
        ],
        cwd=str(tmp_path),
    )
    assert process.pid == 2468
    payload = requests[0][1]
    assert Path(payload["executable"]).name == "pythonw.exe"
    assert "--startup-receipt" in payload["arguments"]
    assert Path(payload["receipt_path"]).name == "desktop-execution-1.json"


def test_desktop_start_failure_reaches_worker_error_handling(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        employee_process.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, '{"success":false,"message":"请先登录员工机桌面"}', ""
        ),
    )
    with pytest.raises(OSError, match="请先登录员工机桌面"):
        employee_process.start_employee_process(
            [
                str(tmp_path / "python.exe"),
                "manager_worker_async.py",
                "--state-root",
                str(tmp_path),
                "--remote-execution-id",
                "execution-1",
            ],
            cwd=str(tmp_path),
        )
