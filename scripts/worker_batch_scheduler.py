"""Stable entry point for the role-owned worker_batch_scheduler implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('worker_batch_scheduler')
else:
    load_module('worker_batch_scheduler', __name__)
