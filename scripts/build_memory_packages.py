"""Stable entry point for the role-owned build_memory_packages implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('build_memory_packages')
else:
    load_module('build_memory_packages', __name__)
