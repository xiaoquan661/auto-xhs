from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(os.name != "nt", reason="PowerShell bootstrap is Windows-only")
def test_bootstrap_reuses_explicit_python_without_installing() -> None:
    powershell = shutil.which("powershell")
    if not powershell:
        pytest.skip("Windows PowerShell is unavailable")

    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["XHS_PYTHON"] = sys.executable
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(project_root / "scripts" / "bootstrap.ps1"),
        ],
        cwd=project_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["status"] == "ready"
    assert report["source"] == "explicit"
    assert Path(report["python_executable"]).resolve() == Path(sys.executable).resolve()
    assert report["needs_user_consent"] is False
