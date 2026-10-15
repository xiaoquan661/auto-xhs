"""Stable entry point for the role-owned debug_common implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('debug_common')
else:
    load_module('debug_common', __name__)
