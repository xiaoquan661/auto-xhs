"""Stable entry point for the role-owned direct_creator_metrics implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('direct_creator_metrics')
else:
    load_module('direct_creator_metrics', __name__)
