"""Stable entry point for the role-owned platforms.spec implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('platforms.spec')
else:
    load_module('platforms.spec', __name__)
