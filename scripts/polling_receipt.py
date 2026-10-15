"""Stable entry point for the role-owned polling_receipt implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('polling_receipt')
else:
    load_module('polling_receipt', __name__)
