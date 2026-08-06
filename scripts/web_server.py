"""Loopback-only HTTP server for the local auto-xhs WebUI."""

from __future__ import annotations

import argparse
import json
import mimetypes
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from .application_service import ApplicationService, ServiceError
except ImportError:  # Direct execution: python scripts/web_server.py
    from application_service import ApplicationService, ServiceError


WEB_ROOT = Path(__file__).resolve().parent.parent / "webui"
API_PREFIX = "/api/v1"


def _validate_bind_host(host: str) -> str:
    if host != "127.0.0.1":
        raise ValueError("本地 WebUI 只允许监听 127.0.0.1")
    return host


def make_handler(
    service: ApplicationService,
    *,
    web_root: Path = WEB_ROOT,
) -> type[BaseHTTPRequestHandler]:
    class LocalWebHandler(BaseHTTPRequestHandler):
        server_version = "auto-xhs-local/1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith(API_PREFIX):
                self._handle_api(path)
                return
            self._serve_static(path)

        def do_POST(self) -> None:
            self._send_json(
                HTTPStatus.METHOD_NOT_ALLOWED,
                {
                    "success": False,
                    "error": {
                        "code": "READ_ONLY_API",
                        "message": "当前 WebUI 增量只开放只读接口",
                    },
                },
            )

        def log_message(self, _format: str, *_args) -> None:
            return

        def _handle_api(self, path: str) -> None:
            try:
                payload = _dispatch_api(service, path)
            except ServiceError as exc:
                self._send_json(exc.http_status, exc.to_dict())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "success": False,
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "本地服务处理请求失败，请查看诊断",
                        },
                    },
                )
                return
            self._send_json(HTTPStatus.OK, payload)

        def _serve_static(self, path: str) -> None:
            relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
            target = (web_root / relative).resolve()
            try:
                target.relative_to(web_root.resolve())
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, status: int, payload: dict) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return LocalWebHandler


def _dispatch_api(service: ApplicationService, path: str) -> dict:
    if path == f"{API_PREFIX}/health":
        return service.health()
    if path == f"{API_PREFIX}/capabilities":
        return service.list_capabilities()
    if path == f"{API_PREFIX}/accounts":
        return service.list_accounts()
    if path == f"{API_PREFIX}/doctor":
        return service.doctor_account()

    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) == 5 and parts[:3] == ["api", "v1", "accounts"]:
        account, action = parts[3], parts[4]
        if action == "status":
            return service.get_account_status(account)
        if action == "doctor":
            return service.doctor_account(account)
    raise ServiceError("NOT_FOUND", "接口不存在", 404)


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    host = _validate_bind_host(host)
    server = ThreadingHTTPServer((host, port), make_handler(ApplicationService()))
    print(f"auto-xhs WebUI: http://{host}:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 auto-xhs 本地只读 WebUI")
    parser.add_argument("--host", default="127.0.0.1", help="固定为 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="本地 WebUI 端口")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    serve(args.host, args.port)
