"""Stable entry point for the role-owned xhs.publish_links implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.publish_links')
else:
    load_module('xhs.publish_links', __name__)
