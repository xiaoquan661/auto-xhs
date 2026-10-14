"""Stable entry point for the role-owned compound_receipt implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('compound_receipt')
else:
    load_module('compound_receipt', __name__)
