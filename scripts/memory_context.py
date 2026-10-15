"""Stable entry point for the role-owned memory_context implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('memory_context')
else:
    load_module('memory_context', __name__)
