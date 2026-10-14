"""Stable entry point for the role-owned scheduled_daily_batches implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('scheduled_daily_batches')
else:
    load_module('scheduled_daily_batches', __name__)
