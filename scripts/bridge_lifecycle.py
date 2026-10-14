"""Stable entry point for the role-owned bridge_lifecycle implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('bridge_lifecycle')
else:
    load_module('bridge_lifecycle', __name__)
