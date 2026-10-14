"""Start and restart the headless executor service."""

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
from collections.abc import Callable
from pathlib import Path

from process_utils import startup_log_excerpt as _startup_log_excerpt
from project_paths import project_root

try:
    from .executor_server import DEFAULT_EXECUTOR_PORT
    from .product_store import product_root
    from .web_lifecycle import (
        ACTIVE_RESTART_STATES,
        port_is_listening,
        read_tasks,
        service_is_healthy,
    )
except ImportError:  # Direct execution: python scripts/executor_lifecycle.py
    from executor_server import DEFAULT_EXECUTOR_PORT
    from product_store import product_root
    from web_lifecycle import (
        ACTIVE_RESTART_STATES,
        port_is_listening,
        read_tasks,
        service_is_healthy,
    )


def executor_listener_pid(host: str, port: int) -> int | None:
    if sys.platform != "win32":
        state_path = product_root() / "executor-service-process.json"
        if not state_path.exists():
            return None
        return int(json.loads(state_path.read_text(encoding="utf-8"))["pid"])

    result = subprocess.run(
        ["netstat", "-ano", "-p", "TCP"],
        capture_output=True,
        text=True,
        check=False,
    )
    endpoint = f"{host}:{port}"
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[1] == endpoint and parts[3].upper() == "LISTENING":
            return int(parts[4])
    return None


def terminate_executor(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def start_executor_service(
    *,
    python_executable: str,
    project_root: str | Path,
    port: int = DEFAULT_EXECUTOR_PORT,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    health_checker: Callable[[str], bool] = service_is_healthy,
    port_checker: Callable[[str, int], bool] = port_is_listening,
    pid_reader: Callable[[str, int], int | None] = executor_listener_pid,
) -> dict:
    root = Path(project_root).resolve()
    url = f"http://127.0.0.1:{port}"
    if health_checker(url):
        return {"success": True, "status": "already_running", "url": url}

    if port_checker("127.0.0.1", port):
        return {
            "success": False,
            "status": "port_in_use",
            "message": f"端口 {port} 已被其他或异常进程占用，无法启动执行服务",
            "url": url,
        }

    log_root = root / "tmp"
    log_root.mkdir(parents=True, exist_ok=True)
    startup_log = log_root / "executor-service-startup.log"
    log_stream = startup_log.open("w", encoding="utf-8")
    kwargs: dict = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": log_stream,
        "stderr": subprocess.STDOUT,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_BREAKAWAY_FROM_JOB
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = process_factory(
            [
                python_executable,
                str(root / "scripts" / "executor_server.py"),
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
            service_pid = pid_reader("127.0.0.1", port) or process.pid
            state_root = product_root()
            state_root.mkdir(parents=True, exist_ok=True)
            (state_root / "executor-service-process.json").write_text(
                json.dumps(
                    {"pid": service_pid, "url": url, "started_by": "auto-xhs-manager"},
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            return {"success": True, "status": "started", "url": url, "pid": service_pid}
        poll = getattr(process, "poll", None)
        if callable(poll) and poll() is not None:
            detail = _startup_log_excerpt(startup_log)
            result = {
                "success": False,
                "status": "process_exited",
                "message": "执行服务进程启动后立即退出",
                "url": url,
                "log_path": str(startup_log),
            }
            if detail:
                result["detail"] = detail
            return result
    return {
        "success": False,
        "status": "start_failed",
        "message": "执行服务未能在 5 秒内启动",
        "url": url,
        "log_path": str(startup_log),
    }


def restart_executor_service(
    *,
    python_executable: str,
    project_root: str | Path,
    port: int = DEFAULT_EXECUTOR_PORT,
    delay: float = 0.0,
    task_reader: Callable[[str], list[dict]] = read_tasks,
    pid_reader: Callable[[str, int], int | None] = executor_listener_pid,
    process_terminator: Callable[[int], None] = terminate_executor,
    health_checker: Callable[[str], bool] = service_is_healthy,
    port_checker: Callable[[str, int], bool] = port_is_listening,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
) -> dict:
    url = f"http://127.0.0.1:{port}"
    if delay > 0:
        time.sleep(delay)

    if not health_checker(url):
        return start_executor_service(
            python_executable=python_executable,
            project_root=project_root,
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
            "message": "执行服务正常响应，但未找到对应进程",
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
            "message": "原执行服务未能在 5 秒内停止",
            "url": url,
            "pid": pid,
        }

    result = start_executor_service(
        python_executable=python_executable,
        project_root=project_root,
        port=port,
        process_factory=process_factory,
        health_checker=health_checker,
        port_checker=port_checker,
    )
    if result.get("success"):
        result["status"] = "restarted"
        result["previous_pid"] = pid
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="管理 auto-xhs 执行机执行服务")
    parser.add_argument("command", choices=["start", "restart"])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--project-root", default=str(project_root()))
    parser.add_argument("--port", type=int, default=DEFAULT_EXECUTOR_PORT)
    parser.add_argument("--delay", type=float, default=0.0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    lifecycle = restart_executor_service if args.command == "restart" else start_executor_service
    values = {
        "python_executable": args.python,
        "project_root": args.project_root,
        "port": args.port,
    }
    if args.command == "restart":
        values["delay"] = args.delay
    result = lifecycle(**values)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
