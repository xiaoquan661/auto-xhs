"""Stable entry point for the role-owned business_runner implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('business_runner')
else:
    load_module('business_runner', __name__)
