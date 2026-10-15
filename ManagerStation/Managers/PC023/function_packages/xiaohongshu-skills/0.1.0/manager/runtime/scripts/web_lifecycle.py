"""Start the local WebUI once and optionally open the default browser."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import argparse
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path

from process_utils import startup_log_excerpt as _startup_log_excerpt
from product_store import product_root
from project_paths import project_root
from service_health import (
    ACTIVE_RESTART_STATES as ACTIVE_RESTART_STATES,
)
from service_health import (
    port_is_listening as port_is_listening,
)
from service_health import (
    read_tasks as read_tasks,
)
from service_health import (
    service_is_healthy as service_is_healthy,
)


def listener_pid(host: str, port: int) -> int | None:
    if sys.platform != "win32":
        state_path = product_root() / "webui-process.json"
        if not state_path.exists():
            return None
        return int(json.loads(state_path.read_text(encoding="utf-8"))["pid"])

    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        check=False,
    )
    endpoints = {f"{host}:{port}", f"0.0.0.0:{port}"}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1] in endpoints and parts[3].upper() == "LISTENING":
            return int(parts[4])
    return None


def terminate_process(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def start_webui(
    *,
    python_executable: str,
    project_root: str | Path,
    host: str = "0.0.0.0",
    port: int = 8765,
    open_browser: bool = False,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    health_checker: Callable[[str], bool] = service_is_healthy,
    port_checker: Callable[[str, int], bool] = port_is_listening,
) -> dict:
    root = Path(project_root).resolve()
    url = f"http://127.0.0.1:{port}"
    if health_checker(url):
        if open_browser:
            webbrowser.open(url)
        return {"success": True, "status": "already_running", "url": url}

    if port_checker("127.0.0.1", port):
        return {
            "success": False,
            "status": "port_in_use",
            "message": f"端口 {port} 已被其他或异常进程占用，无法启动本地服务",
            "url": url,
        }

    log_root = root / "tmp"
    log_root.mkdir(parents=True, exist_ok=True)
    startup_log = log_root / "webui-startup.log"
    log_stream = startup_log.open("w", encoding="utf-8")

    kwargs: dict = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": log_stream,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = process_factory(
            [
                python_executable,
                str(root / "scripts" / "web_server.py"),
                "--host",
                host,
                "--port",
                str(port),
            ],
            **kwargs,
        )
    finally:
        log_stream.close()

    for _ in range(50):
        time.sleep(0.1)
        if health_checker(url):
            state_root = product_root()
            state_root.mkdir(parents=True, exist_ok=True)
            (state_root / "webui-process.json").write_text(
                json.dumps(
                    {"pid": process.pid, "url": url, "host": host, "started_by": "auto-xhs"},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            if open_browser:
                webbrowser.open(url)
            return {"success": True, "status": "started", "url": url, "pid": process.pid}
        poll = getattr(process, "poll", None)
        if callable(poll) and poll() is not None:
            detail = _startup_log_excerpt(startup_log)
            result = {
                "success": False,
                "status": "process_exited",
                "message": "本地服务进程启动后立即退出",
                "url": url,
                "log_path": str(startup_log),
            }
            if detail:
                result["detail"] = detail
            return result
    return {
        "success": False,
        "status": "start_failed",
        "message": "本地服务未能在 5 秒内启动",
        "url": url,
        "log_path": str(startup_log),
    }


def restart_webui(
    *,
    python_executable: str,
    project_root: str | Path,
    host: str = "0.0.0.0",
    port: int = 8765,
    delay: float = 0.0,
    task_reader: Callable[[str], list[dict]] = read_tasks,
    pid_reader: Callable[[str, int], int | None] = listener_pid,
    process_terminator: Callable[[int], None] = terminate_process,
    health_checker: Callable[[str], bool] = service_is_healthy,
    port_checker: Callable[[str, int], bool] = port_is_listening,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> dict:
    url = f"http://127.0.0.1:{port}"
    if delay > 0:
        time.sleep(delay)

    if not health_checker(url):
        return start_webui(
            python_executable=python_executable,
            project_root=project_root,
            host=host,
            port=port,
            process_factory=process_factory,
            health_checker=health_checker,
            port_checker=port_checker,
        )

    active = [
        item
        for item in task_reader(url)
        if str(item.get("state") or "").upper() in ACTIVE_RESTART_STATES
    ]
    if active:
        return {
            "success": False,
            "status": "active_tasks",
            "message": f"当前有 {len(active)} 个执行中任务，请先终止或等待任务结束",
            "task_ids": [str(item.get("task_id") or "") for item in active],
            "url": url,
        }

    pid = pid_reader("127.0.0.1", port)
    if pid is None:
        return {
            "success": False,
            "status": "process_not_found",
            "message": "本地服务正常响应，但未找到对应进程",
            "url": url,
        }

    process_terminator(pid)
    for _ in range(50):
        if not port_checker("127.0.0.1", port):
            break
        time.sleep(0.1)
    else:
        return {
            "success": False,
            "status": "stop_failed",
            "message": "旧的本地服务未能在 5 秒内停止",
            "url": url,
            "pid": pid,
        }

    result = start_webui(
        python_executable=python_executable,
        project_root=project_root,
        host=host,
        port=port,
        process_factory=process_factory,
        health_checker=health_checker,
        port_checker=port_checker,
    )
    if result.get("success"):
        result["status"] = "restarted"
        result["previous_pid"] = pid
    return result


def schedule_webui_restart(
    *,
    python_executable: str,
    project_root: str | Path,
    host: str = "0.0.0.0",
    port: int = 8765,
    delay: float = 0.8,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> dict:
    root = Path(project_root).resolve()
    log_root = root / "tmp"
    log_root.mkdir(parents=True, exist_ok=True)
    restart_log = log_root / "webui-restart.log"
    log_stream = restart_log.open("w", encoding="utf-8")
    kwargs: dict = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": log_stream,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    try:
        process = process_factory(
            [
                python_executable,
                str(Path(__file__).resolve()),
                "restart",
                "--python",
                python_executable,
                "--project-root",
                str(root),
                "--host",
                host,
                "--port",
                str(port),
                "--delay",
                str(delay),
            ],
            **kwargs,
        )
    finally:
        log_stream.close()
    return {
        "success": True,
        "status": "restart_scheduled",
        "helper_pid": process.pid,
        "url": f"http://127.0.0.1:{port}",
        "log_path": str(restart_log),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 auto-xhs 本地 WebUI")
    parser.add_argument("command", choices=["start", "restart"])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--project-root", default=str(project_root()))
    parser.add_argument("--host", default="0.0.0.0", choices=["0.0.0.0", "127.0.0.1"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    parser.add_argument("--delay", type=float, default=0.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "restart":
        result = restart_webui(
            python_executable=args.python,
            project_root=args.project_root,
            host=args.host,
            port=args.port,
            delay=args.delay,
        )
    else:
        result = start_webui(
            python_executable=args.python,
            project_root=args.project_root,
            host=args.host,
            port=args.port,
            open_browser=args.open_browser,
        )
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
