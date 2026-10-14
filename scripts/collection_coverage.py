"""Stable entry point for the role-owned collection_coverage implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('collection_coverage')
else:
    load_module('collection_coverage', __name__)
