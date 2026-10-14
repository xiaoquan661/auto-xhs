"""Stable entry point for the role-owned xhs.private_inbox implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.private_inbox')
else:
    load_module('xhs.private_inbox', __name__)
