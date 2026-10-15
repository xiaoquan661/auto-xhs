"""Load the project's role-owned sources through stable Python entry points."""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import json
import runpy
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_LAYOUT = json.loads((_ROOT / "source-layout.json").read_text(encoding="utf-8"))
_MODULES: dict[str, str] = _LAYOUT["module_files"]


def get_layout() -> dict[str, Any]:
    return _LAYOUT


def project_root() -> Path:
    return _ROOT


def canonical_path(module_name: str) -> Path:
    return _ROOT / _MODULES[module_name]


def _publish_aliases(name: str, module: ModuleType) -> None:
    for alias in (name, f"scripts.{name}"):
        sys.modules[alias] = module
        parent, _, child = alias.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], child, module)


def load_module(name: str, alias: str | None = None) -> ModuleType:
    current = sys.modules.get(name)
    if current is not None and Path(current.__file__).resolve() != canonical_path(name).resolve():
        # The first stable entry installs this finder while its own facade is loading.
        sys.modules.pop(name)
    module = importlib.import_module(name)
    _publish_aliases(name, module)
    if alias:
        sys.modules[alias] = module
    return module


class _RoleLoader(importlib.abc.Loader):
    def __init__(self, name: str) -> None:
        self.name = name

    def create_module(self, spec: Any) -> ModuleType:
        return load_module(self.name)

    def exec_module(self, module: ModuleType) -> None:
        # Both public import forms retain the canonical module's origin and globals.
        path = canonical_path(self.name)
        module.__spec__ = importlib.util.spec_from_file_location(
            self.name,
            path,
            submodule_search_locations=[str(path.parent)] if path.name == "__init__.py" else None,
        )


class _RoleFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        name = fullname.removeprefix("scripts.")
        if name not in _MODULES:
            return None
        source = canonical_path(name)
        if fullname == name:
            return importlib.util.spec_from_file_location(
                name,
                source,
                submodule_search_locations=[str(source.parent)]
                if source.name == "__init__.py"
                else None,
            )
        return importlib.util.spec_from_loader(
            fullname, _RoleLoader(name), is_package=source.name == "__init__.py"
        )


def run_entrypoint(name: str) -> None:
    runpy.run_path(str(canonical_path(name)), run_name="__main__")


sys.modules["_role_layout"] = sys.modules[__name__]
sys.modules["scripts._role_layout"] = sys.modules[__name__]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
sys.meta_path.insert(0, _RoleFinder())
