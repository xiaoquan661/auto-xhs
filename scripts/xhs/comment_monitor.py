"""Stable entry point for the role-owned xhs.comment_monitor implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.comment_monitor')
else:
    load_module('xhs.comment_monitor', __name__)
