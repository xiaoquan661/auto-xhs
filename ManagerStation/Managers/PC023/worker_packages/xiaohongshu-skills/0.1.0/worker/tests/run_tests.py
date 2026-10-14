"""Run the role package's selected tests from the project test baseline."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="运行职责源包关联的项目回归测试")
    parser.add_argument("--collect-only", action="store_true", help="仅核对测试入口与用例收集")
    args = parser.parse_args()
    project_root = next(
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    selection = json.loads(Path(__file__).with_name("selection.json").read_text(encoding="utf-8"))
    command = [sys.executable, "-m", "pytest", *selection["test_paths"], "-q"]
    if args.collect_only:
        command.append("--collect-only")
    return subprocess.call(command, cwd=project_root)


if __name__ == "__main__":
    raise SystemExit(main())
