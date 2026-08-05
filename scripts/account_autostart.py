"""Windows logon autostart registration for account-scoped Bridge runtimes."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from account_manager import validate_account_name

TASK_PREFIX = "auto-xhs-bridge-"


def task_name(account: str) -> str:
    return TASK_PREFIX + validate_account_name(account)


def task_action(account: str) -> str:
    account = validate_account_name(account)
    launcher = Path(__file__).with_name("autostart.ps1").resolve()
    return (
        'powershell.exe -NoProfile -ExecutionPolicy Bypass '
        f'-File "{launcher}" -Account "{account}"'
    )


def account_autostart_status(account: str) -> dict:
    _require_windows()
    name = task_name(account)
    result = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", name],
        check=False,
        capture_output=True,
        text=True,
    )
    return {
        "account": validate_account_name(account),
        "task_name": name,
        "enabled": result.returncode == 0,
    }


def enable_account_autostart(account: str) -> dict:
    _require_windows()
    account = validate_account_name(account)
    name = task_name(account)
    action = task_action(account)
    result = subprocess.run(
        [
            "schtasks.exe",
            "/Create",
            "/TN",
            name,
            "/SC",
            "ONLOGON",
            "/TR",
            action,
            "/RL",
            "LIMITED",
            "/F",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"Failed to register Bridge autostart task: {detail}")
    return {
        "account": account,
        "task_name": name,
        "enabled": True,
        "trigger": "user_logon",
    }


def disable_account_autostart(account: str) -> dict:
    _require_windows()
    account = validate_account_name(account)
    name = task_name(account)
    result = subprocess.run(
        ["schtasks.exe", "/Delete", "/TN", name, "/F"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in {0, 1}:
        detail = (result.stderr or result.stdout or "unknown error").strip()
        raise RuntimeError(f"Failed to remove Bridge autostart task: {detail}")
    return {"account": account, "task_name": name, "enabled": False}


def _require_windows() -> None:
    if os.name != "nt":
        raise RuntimeError("Bridge logon autostart is currently supported only on Windows")
