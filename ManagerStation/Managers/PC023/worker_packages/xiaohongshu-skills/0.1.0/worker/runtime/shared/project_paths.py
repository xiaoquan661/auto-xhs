"""Project source and resource paths shared by manager and worker runtimes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from _role_layout import canonical_path as canonical_path
from _role_layout import get_layout as get_layout
from _role_layout import project_root as project_root


def legacy_script_path(filename: str) -> Path:
    return project_root() / "scripts" / filename


def resource_root(name: str) -> Path:
    return project_root() / get_layout()["resource_roots"][name]


def extension_root() -> Path:
    return resource_root("extension")


def prompts_root() -> Path:
    return resource_root("prompts")


def skills_root() -> Path:
    return resource_root("skills")


def webui_layout() -> dict[str, Any]:
    return json.loads((project_root() / "webui-layout.json").read_text(encoding="utf-8"))


def webui_asset_path(relative: str) -> Path:
    return project_root() / webui_layout()["assets"][relative]
