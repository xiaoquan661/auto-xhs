"""Stable entry point for the role-owned worker_data_export implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('worker_data_export')
else:
    load_module('worker_data_export', __name__)
