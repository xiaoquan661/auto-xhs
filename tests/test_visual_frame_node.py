from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def test_visual_frame_browser_regressions() -> None:
    node = shutil.which("node") or str(
        Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe"
    )
    result = subprocess.run(
        [
            node,
            "--test",
            str(Path(__file__).with_suffix(".cjs").with_name("test_visual_browser.cjs")),
        ],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
