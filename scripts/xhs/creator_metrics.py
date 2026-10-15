"""Stable entry point for the role-owned xhs.creator_metrics implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.creator_metrics')
else:
    load_module('xhs.creator_metrics', __name__)
