"""Stable entry point for the role-owned service_errors implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('service_errors')
else:
    load_module('service_errors', __name__)
