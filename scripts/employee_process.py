"""Start employee execution in the logged-in Windows user's session."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any


def start_employee_process(command: list[str], **kwargs: Any) -> Any:
    if os.name != "nt":
        return subprocess.Popen(command, **kwargs)
    state_root = Path(command[command.index("--state-root") + 1])
    execution_id = command[command.index("--remote-execution-id") + 1]
    receipt = state_root / "logs" / f"desktop-{execution_id}.json"
    pythonw = Path(command[0]).with_name("pythonw.exe")
    payload = {
        "executable": str(pythonw),
        "arguments": subprocess.list2cmdline([*command[1:], "--startup-receipt", str(receipt)]),
        "working_directory": kwargs["cwd"],
        "receipt_path": str(receipt),
    }
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(Path(__file__).with_name("start-employee-desktop.ps1")),
            ],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        result = json.loads(completed.stdout.lstrip("\ufeff"))
    except (subprocess.TimeoutExpired, ValueError) as exc:
        raise OSError("员工桌面执行进程未返回启动回执，请检查桌面会话") from exc
    if completed.returncode or not result.get("success"):
        raise OSError(result.get("message") or "员工桌面执行进程启动失败")
    return SimpleNamespace(pid=int(result["pid"]))
