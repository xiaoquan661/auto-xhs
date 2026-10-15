"""Stable entry point for the role-owned run_lock implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('run_lock')
else:
    load_module('run_lock', __name__)
