"""Stable entry point for the role-owned account_lifecycle implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('account_lifecycle')
else:
    load_module('account_lifecycle', __name__)
