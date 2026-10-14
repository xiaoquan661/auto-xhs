"""Stable entry point for the role-owned visual_browser implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('visual_browser')
else:
    load_module('visual_browser', __name__)
