from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from project_paths import project_root


def test_worker_package_imports_and_entries_without_manager_sources(tmp_path: Path) -> None:
    source = project_root()
    spec = importlib.util.spec_from_file_location(
        "worker_builder", source / "deploy/build-worker-layout.py"
    )
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    destination = tmp_path / "PC065"
    result = builder.build(source, destination, "PC065")
    assert result["file_count"] > 250
    assert not (destination / "ManagerStation").exists()
    layout = json.loads((destination / "source-layout.json").read_text(encoding="utf-8"))
    assert layout["workers"] == ["PC065"]
    assert all(path.startswith("WorkerStation/") for path in layout["module_files"].values())
    probe = (
        "import sys, json, importlib; sys.path.insert(0, sys.argv[1]); import _role_layout; "
        "[importlib.import_module(name) for name in _role_layout.get_layout()['module_files']]; "
        "import executor_server, manager_worker_service; "
        "assert 'web_server' not in sys.modules and 'manager_service' not in sys.modules; "
        "print(json.dumps([executor_server.__file__, manager_worker_service.__file__]))"
    )
    imported = subprocess.run(
        [sys.executable, "-c", probe, str(destination / "scripts")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert all("WorkerStation" in path for path in json.loads(imported.stdout))
    for entry in ("cli.py", "employee_task_cli.py", "executor_server.py"):
        subprocess.run(
            [sys.executable, str(destination / "scripts" / entry), "--help"],
            cwd=tmp_path,
            capture_output=True,
            check=True,
        )
    assert (destination / layout["resource_roots"]["extension"] / "bridge_config.js").is_file()
