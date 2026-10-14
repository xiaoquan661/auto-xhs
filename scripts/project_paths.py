"""Stable entry point for the role-owned project_paths implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('project_paths')
else:
    load_module('project_paths', __name__)
