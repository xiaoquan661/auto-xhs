"""Stable entry point for the role-owned operations_db implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('operations_db')
else:
    load_module('operations_db', __name__)
