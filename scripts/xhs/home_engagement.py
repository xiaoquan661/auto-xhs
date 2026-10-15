"""Stable entry point for the role-owned xhs.home_engagement implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.home_engagement')
else:
    load_module('xhs.home_engagement', __name__)
