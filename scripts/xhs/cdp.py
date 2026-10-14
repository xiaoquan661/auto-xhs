"""Stable entry point for the role-owned xhs.cdp implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.cdp')
else:
    load_module('xhs.cdp', __name__)
