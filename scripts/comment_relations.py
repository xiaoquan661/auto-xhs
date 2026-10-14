"""Stable entry point for the role-owned comment_relations implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('comment_relations')
else:
    load_module('comment_relations', __name__)
