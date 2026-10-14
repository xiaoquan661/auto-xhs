"""Stable entry point for the role-owned manager_application_memory implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('manager_application_memory')
else:
    load_module('manager_application_memory', __name__)
