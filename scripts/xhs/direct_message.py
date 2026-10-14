"""Stable entry point for the role-owned xhs.direct_message implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.direct_message')
else:
    load_module('xhs.direct_message', __name__)
