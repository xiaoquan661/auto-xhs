"""Stable entry point for the role-owned reply_rule_service implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('reply_rule_service')
else:
    load_module('reply_rule_service', __name__)
