"""Stable product service errors shared by CLI and the local HTTP API."""

from __future__ import annotations


class ServiceError(RuntimeError):
    """Application error with a stable code and HTTP mapping."""

    def __init__(self, code: str, message: str, http_status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status

    def to_dict(self) -> dict:
        return {
            "success": False,
            "error": {"code": self.code, "message": self.message},
        }
