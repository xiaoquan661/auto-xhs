"""Stable entry point for the role-owned xhs.like_favorite implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.like_favorite')
else:
    load_module('xhs.like_favorite', __name__)
