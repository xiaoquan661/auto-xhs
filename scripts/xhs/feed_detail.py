"""Stable entry point for the role-owned xhs.feed_detail implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.feed_detail')
else:
    load_module('xhs.feed_detail', __name__)
