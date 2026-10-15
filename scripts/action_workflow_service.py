"""Stable entry point for the role-owned action_workflow_service implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('action_workflow_service')
else:
    load_module('action_workflow_service', __name__)
