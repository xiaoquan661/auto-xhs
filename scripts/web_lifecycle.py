"""Start the local WebUI once and optionally open the default browser."""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from product_store import product_root


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


def _startup_log_excerpt(path: Path, *, limit: int = 1200) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return content[-limit:]


def start_webui(
    *,
    python_executable: str,
    project_root: str | Path,
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
        kwargs["creationflags"] = (
            subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        )
    else:
        kwargs["start_new_session"] = True
    try:
        process = process_factory(
            [
                python_executable,
                str(root / "scripts" / "web_server.py"),
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
                    {"pid": process.pid, "url": url, "started_by": "auto-xhs"},
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 auto-xhs 本地 WebUI")
    parser.add_argument("command", choices=["start"])
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parent.parent))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    result = start_webui(
        python_executable=args.python,
        project_root=args.project_root,
        port=args.port,
        open_browser=args.open_browser,
    )
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
