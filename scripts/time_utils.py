"""Shared time conversion helpers."""

from __future__ import annotations

from datetime import UTC, datetime


def epoch_to_iso(value: int) -> str:
    if not value:
        return ""
    seconds = value / 1000 if value > 10_000_000_000 else value
    return datetime.fromtimestamp(seconds, UTC).isoformat()
