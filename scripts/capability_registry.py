"""Stable entry point for the role-owned capability_registry implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('capability_registry')
else:
    load_module('capability_registry', __name__)
