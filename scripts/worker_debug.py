"""Stable entry point for the role-owned worker_debug implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('worker_debug')
else:
    load_module('worker_debug', __name__)
