"""Registered lifecycle management for per-account Bridge processes."""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from account_manager import AccountConfig, account_dir
from run_lock import _windows_process_is_running
from xhs import bridge as bridge_module


def bridge_process_path(account: str) -> Path:
    return account_dir(account) / "bridge-process.json"


def get_bridge_lifecycle(
    config: AccountConfig,
    *,
    page_factory=None,
    process_checker=None,
) -> dict:
    page_factory = page_factory or bridge_module.BridgePage
    bridge_url = getattr(config, "bridge_url", f"ws://localhost:{config.bridge_port}")
    page = page_factory(bridge_url, account=config.name)
    running = page.is_server_running()
    record = _read_process_record(config.name)
    checker = process_checker or process_is_running
    pid_running = bool(record and checker(int(record["pid"])))
    if record and not pid_running:
        _clear_process_record(config.name)
        record = None
    return {
        "account": config.name,
        "bridge_running": bool(running),
        "registered": bool(record),
        "pid": int(record["pid"]) if record else None,
        "pid_running": pid_running,
        "started_at": record.get("started_at") if record else None,
    }


def start_bridge(
    config: AccountConfig,
    *,
    page_factory=None,
    process_factory=None,
    sleep=time.sleep,
    attempts: int = 10,
) -> dict:
    if page_factory is None:
        page_factory = bridge_module.BridgePage
    process_factory = process_factory or subprocess.Popen
    bridge_url = getattr(config, "bridge_url", f"ws://localhost:{config.bridge_port}")
    page = page_factory(bridge_url, account=config.name)
    if page.is_server_running():
        return get_bridge_lifecycle(config, page_factory=page_factory)

    bridge_port = urlparse(bridge_url).port or config.bridge_port
    scripts_dir = Path(__file__).resolve().parent
    process_env = os.environ.copy()
    for key, value in (
        ("XHS_BRIDGE_ACCOUNT_ID", getattr(config, "account_id", None)),
        ("XHS_BRIDGE_TOKEN", getattr(config, "bridge_token", None)),
        ("XHS_EXTENSION_INSTANCE_ID", getattr(config, "extension_instance_id", None)),
    ):
        if value:
            process_env[key] = value
    kwargs: dict = {
        "env": process_env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    process = process_factory(
        [
            sys.executable,
            str(scripts_dir / "bridge_server.py"),
            "--port",
            str(bridge_port),
            "--account",
            config.name,
            "--profile-directory",
            getattr(config, "chrome_profile_directory", None) or "Default",
        ],
        **kwargs,
    )
    _write_process_record(config.name, int(process.pid), bridge_port)
    for _ in range(attempts):
        sleep(1)
        if page.is_server_running():
            return get_bridge_lifecycle(config, page_factory=page_factory)
    _clear_process_record(config.name)
    raise RuntimeError("Bridge 启动超时，请查看诊断报告")


def stop_bridge(
    config: AccountConfig,
    *,
    page_factory=None,
    process_checker=None,
    sleep=time.sleep,
    attempts: int = 10,
) -> dict:
    if page_factory is None:
        page_factory = bridge_module.BridgePage
    record = _read_process_record(config.name)
    checker = process_checker or process_is_running
    if not record or not checker(int(record["pid"])):
        _clear_process_record(config.name)
        raise RuntimeError("没有找到由本项目登记的 Bridge 进程")
    bridge_url = getattr(config, "bridge_url", f"ws://localhost:{config.bridge_port}")
    page = page_factory(bridge_url, account=config.name)
    accepted = page.shutdown_server(
        account_id=getattr(config, "account_id", None),
        bridge_token=getattr(config, "bridge_token", None),
    )
    if not accepted:
        raise RuntimeError("Bridge 未接受停止请求")
    for _ in range(attempts):
        sleep(0.2)
        if not page.is_server_running():
            _clear_process_record(config.name)
            return {
                "account": config.name,
                "bridge_running": False,
                "registered": False,
                "pid": None,
                "pid_running": False,
                "started_at": None,
            }
    raise RuntimeError("Bridge 正在停止，请稍后刷新状态")


def restart_bridge(
    config: AccountConfig,
    *,
    page_factory=None,
    process_factory=None,
    process_checker=None,
    sleep=time.sleep,
    attempts: int = 10,
) -> dict:
    stop_bridge(
        config,
        page_factory=page_factory,
        process_checker=process_checker,
        sleep=sleep,
        attempts=attempts,
    )
    return start_bridge(
        config,
        page_factory=page_factory,
        process_factory=process_factory,
        sleep=sleep,
        attempts=attempts,
    )


def process_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_is_running(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_process_record(account: str) -> dict | None:
    path = bridge_process_path(account)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("account") != account or int(value["pid"]) <= 0:
            return None
        return value
    except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError):
        return None


def _write_process_record(account: str, pid: int, port: int) -> None:
    path = bridge_process_path(account)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(
            {
                "account": account,
                "pid": pid,
                "port": port,
                "started_at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _clear_process_record(account: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        bridge_process_path(account).unlink()
