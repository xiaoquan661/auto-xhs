"""Validate navigation and publish controls when installing the session update."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_session_extension() -> None:
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node") or str(
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    result = subprocess.run(
        [node, "--test", "tests/test_navigation_readiness.cjs",
         "tests/test_publish_native_click.cjs"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
