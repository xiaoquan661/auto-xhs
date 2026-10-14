"""Stable entry point for the role-owned quota_service implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('quota_service')
else:
    load_module('quota_service', __name__)
