"""Stable entry point for the role-owned manager_dashboard_reader implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('manager_dashboard_reader')
else:
    load_module('manager_dashboard_reader', __name__)
