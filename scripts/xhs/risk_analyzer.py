"""Stable entry point for the role-owned xhs.risk_analyzer implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('xhs.risk_analyzer')
else:
    load_module('xhs.risk_analyzer', __name__)
