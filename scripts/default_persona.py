"""Stable entry point for the role-owned default_persona implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('default_persona')
else:
    load_module('default_persona', __name__)
