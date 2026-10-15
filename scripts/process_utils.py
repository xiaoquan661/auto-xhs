"""Stable entry point for the role-owned process_utils implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('process_utils')
else:
    load_module('process_utils', __name__)
