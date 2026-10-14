"""Stable entry point for the role-owned operations_acceptance implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('operations_acceptance')
else:
    load_module('operations_acceptance', __name__)
