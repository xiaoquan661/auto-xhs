"""Simple same-origin session protection for local WebUI mutations."""

from __future__ import annotations

import secrets

SESSION_HEADER = "X-Auto-XHS-Session"
MAX_JSON_BODY = 64 * 1024


def create_session_token() -> str:
    return secrets.token_urlsafe(24)


def session_bootstrap(token: str) -> dict:
    return {"success": True, "session_token": token}
