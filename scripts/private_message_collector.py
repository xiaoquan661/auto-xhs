"""Stable entry point for the role-owned private_message_collector implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('private_message_collector')
else:
    load_module('private_message_collector', __name__)
