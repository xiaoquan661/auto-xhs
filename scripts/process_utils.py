"""Shared helpers for managed service processes."""

from __future__ import annotations

from pathlib import Path


def startup_log_excerpt(path: Path, *, limit: int = 1200) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return content[-limit:]
