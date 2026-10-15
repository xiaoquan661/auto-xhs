"""Stable entry point for the role-owned comment_receipt_review implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('comment_receipt_review')
else:
    load_module('comment_receipt_review', __name__)
