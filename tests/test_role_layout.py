from __future__ import annotations

import importlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.project_paths import canonical_path, get_layout, project_root


@pytest.mark.parametrize(
    "name", ["manager_service", "manager_worker_service", "account_memory", "xhs.errors"]
)
def test_stable_imports_share_the_canonical_module(name: str) -> None:
    bare = importlib.import_module(name)
    public = importlib.import_module(f"scripts.{name}")
    alias = f"legacy_layout_{name.replace('.', '_')}"
    facade = project_root() / "scripts" / Path(*name.split(".")).with_suffix(".py")
    spec = importlib.util.spec_from_file_location(alias, facade)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    try:
        spec.loader.exec_module(module)
        assert sys.modules[alias] is bare is public
        assert Path(bare.__file__).resolve() == canonical_path(name).resolve()
    finally:
        sys.modules.pop(alias, None)


@pytest.mark.parametrize(
    ("name", "option"),
    [("cli", "--account"), ("manager_cli", "machine"), ("executor_server", "--port")],
)
def test_canonical_help_starts_from_an_external_directory(
    name: str, option: str, tmp_path: Path
) -> None:
    env = {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "XHS_PRODUCT_HOME": str(tmp_path / "product"),
        "XHS_MANAGER_HOME": str(tmp_path / "manager"),
    }
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, "-B", str(canonical_path(name)), "--help"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert option in result.stdout
    assert not (tmp_path / "product").exists()
    assert not (tmp_path / "manager").exists()


def test_layout_resources_resolve_to_the_delivered_role_sources() -> None:
    root = project_root()
    layout = get_layout()
    for name in ("extension", "prompts", "skills"):
        assert (root / layout["resource_roots"][name]).is_dir()
    assets = json.loads((root / "webui-layout.json").read_text(encoding="utf-8"))["assets"]
    assert all((root / relative).is_file() for relative in assets.values())
