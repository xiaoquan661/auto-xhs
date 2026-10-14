from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import build_memory_packages as builder


def test_dependency_closure_reads_canonical_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = {
        "manager_cli": "ManagerStation/Managers/PC023/manager/manager_cli.py",
        "manager_service": "ManagerStation/Managers/PC023/manager/manager_service.py",
        "cli": "ManagerStation/Managers/PC023/worker/cli.py",
        "xhs": "ManagerStation/Managers/PC023/worker/xhs/__init__.py",
        "xhs.login": "ManagerStation/Managers/PC023/worker/xhs/login.py",
        "service_errors": "ManagerStation/Managers/PC023/worker/shared/service_errors.py",
        "project_paths": "ManagerStation/Managers/PC023/worker/shared/project_paths.py",
    }
    code = {
        "manager_cli": "from manager_service import ManagerService\n",
        "manager_service": "from service_errors import ServiceError\n",
        "cli": "from xhs import login\n",
        "xhs": "from .login import LOGIN\n",
        "xhs.login": "from service_errors import ServiceError\nLOGIN = True\n",
        "service_errors": "class ServiceError(Exception):\n    pass\n",
        "project_paths": "def project_root():\n    pass\n",
    }
    for module, relative in files.items():
        source = tmp_path / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(code[module], encoding="utf-8")
        facade = (
            tmp_path
            / "scripts"
            / ("xhs/__init__.py" if module == "xhs" else module.replace(".", "/") + ".py")
        )
        facade.parent.mkdir(parents=True, exist_ok=True)
        facade.write_text("from _role_layout import load\n", encoding="utf-8")
    (tmp_path / "scripts/_role_layout.py").write_text("def load():\n    pass\n", encoding="utf-8")
    (tmp_path / "source-layout.json").write_text(
        json.dumps({"module_files": files, "resource_roots": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(builder, "PROJECT", tmp_path)

    selected = set(builder.source_dependencies())

    assert {tmp_path / files[name] for name in files} <= selected
    assert tmp_path / "scripts/_role_layout.py" in selected
    assert tmp_path / "source-layout.json" in selected
    assert tmp_path / "scripts/manager_service.py" in selected
    assert tmp_path / "scripts/xhs/__init__.py" in selected


def test_packaged_memory_runs_from_copied_role_layout(tmp_path: Path) -> None:
    package = tmp_path / "package"
    snapshot = package / "runtime/project"
    for source in builder.snapshot_files():
        target = snapshot / source.relative_to(builder.PROJECT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    runtime = package / "runtime/run.py"
    shutil.copyfile(builder.PROJECT / "package_templates/memory/runtime.py", runtime)
    (package / "manifest.json").write_text(
        json.dumps(
            {"function_id": "MEMORY-01", "package_name": "长期记忆", "allowed_actions": ["save"]}
        ),
        encoding="utf-8",
    )
    state = tmp_path / "state"
    state.mkdir()
    request = state / "request.json"
    request.write_text(
        json.dumps({"layer": "shared_core", "content": "表达依据观察", "source": "验证"}),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(runtime),
            "--action",
            "save",
            "--state-root",
            str(state),
            "--input-file",
            str(request),
        ],
        cwd=state,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["memory"]["content"] == "表达依据观察"

    probe = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys,json;from pathlib import Path;"
                f"p=Path({str(snapshot)!r});sys.path[:0]=[str(p/'scripts'),str(p)];"
                "import manager_service,manager_worker_service,account_memory;"
                "print(json.dumps([m.__file__ for m in "
                "(manager_service,manager_worker_service,account_memory)]))"
            ),
        ],
        cwd=state,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
    layout = json.loads((snapshot / "source-layout.json").read_text(encoding="utf-8"))
    expected = [
        snapshot / layout["module_files"][name]
        for name in ("manager_service", "manager_worker_service", "account_memory")
    ]
    assert [Path(path).resolve() for path in json.loads(probe.stdout)] == [
        path.resolve() for path in expected
    ]
