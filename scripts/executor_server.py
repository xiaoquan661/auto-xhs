"""Stable entry point for the role-owned executor_server implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('executor_server')
else:
    load_module('executor_server', __name__)
