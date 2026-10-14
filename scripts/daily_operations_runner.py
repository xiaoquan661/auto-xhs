"""Stable entry point for the role-owned daily_operations_runner implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('daily_operations_runner')
else:
    load_module('daily_operations_runner', __name__)
