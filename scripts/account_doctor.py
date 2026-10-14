"""Stable entry point for the role-owned account_doctor implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('account_doctor')
else:
    load_module('account_doctor', __name__)
