"""Stable entry point for the role-owned manager_sync_log implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('manager_sync_log')
else:
    load_module('manager_sync_log', __name__)
