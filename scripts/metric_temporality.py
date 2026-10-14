"""Stable entry point for the role-owned metric_temporality implementation."""

from __future__ import annotations

try:
    from scripts._role_layout import load_module, run_entrypoint
except ModuleNotFoundError:
    from _role_layout import load_module, run_entrypoint

if __name__ == "__main__":
    run_entrypoint('metric_temporality')
else:
    load_module('metric_temporality', __name__)
