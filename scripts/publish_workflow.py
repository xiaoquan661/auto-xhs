"""Stable entry point for the role-owned publish_workflow implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('publish_workflow')
else:
    load_module('publish_workflow', __name__)
