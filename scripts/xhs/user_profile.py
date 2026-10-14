"""Stable entry point for the role-owned xhs.user_profile implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.user_profile')
else:
    load_module('xhs.user_profile', __name__)
