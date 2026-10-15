"""Stable entry point for the role-owned manager_employee_operations implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('manager_employee_operations')
else:
    load_module('manager_employee_operations', __name__)
