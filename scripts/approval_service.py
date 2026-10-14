"""Stable entry point for the role-owned approval_service implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('approval_service')
else:
    load_module('approval_service', __name__)
