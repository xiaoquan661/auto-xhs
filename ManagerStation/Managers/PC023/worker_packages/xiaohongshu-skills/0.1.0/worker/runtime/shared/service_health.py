"""Shared local service health and task occupancy checks."""

from __future__ import annotations

import json
import socket
from urllib.error import URLError
from urllib.request import urlopen

ACTIVE_RESTART_STATES = {"RUNNING", "CANCEL_REQUESTED"}


def service_is_healthy(url: str, *, timeout: float = 0.5) -> bool:
    try:
        with urlopen(f"{url}/api/v1/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("status") == "ok"
    except (OSError, URLError, json.JSONDecodeError):
        return False


def port_is_listening(host: str, port: int, *, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def read_tasks(url: str, *, timeout: float = 2.0) -> list[dict]:
    with urlopen(f"{url}/api/v1/tasks", timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return list(payload.get("tasks") or [])


