from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


def test_publish_native_click_bridge() -> None:
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    subprocess.run(
        [node, "--test", "tests/test_publish_native_click.cjs"],
        cwd=Path(__file__).resolve().parents[1], check=True,
    )
