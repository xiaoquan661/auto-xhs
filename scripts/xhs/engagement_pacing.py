"""Stable entry point for the role-owned xhs.engagement_pacing implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.engagement_pacing')
else:
    load_module('xhs.engagement_pacing', __name__)
