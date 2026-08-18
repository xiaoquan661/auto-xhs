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
    from .web_session import MAX_JSON_BODY, SESSION_HEADER, create_session_token
except ImportError:  # Direct execution: python scripts/web_server.py
    from application_service import ApplicationService, ServiceError
    from web_session import MAX_JSON_BODY, SESSION_HEADER, create_session_token


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
    session_token: str | None = None,
) -> type[BaseHTTPRequestHandler]:
    token = session_token or create_session_token()

    class LocalWebHandler(BaseHTTPRequestHandler):
        server_version = "auto-xhs-local/1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith(API_PREFIX):
                self._handle_api(path)
                return
            self._serve_static(path)

        def do_POST(self) -> None:
            self._handle_mutation("POST")

        def do_PATCH(self) -> None:
            self._handle_mutation("PATCH")

        def do_DELETE(self) -> None:
            self._handle_mutation("DELETE")

        def log_message(self, _format: str, *_args) -> None:
            return

        def _handle_api(self, path: str) -> None:
            try:
                payload = (
                    {"success": True, "session_token": token}
                    if path == f"{API_PREFIX}/session"
                    else _dispatch_api(service, path)
                )
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

        def _handle_mutation(self, method: str) -> None:
            path = urlparse(self.path).path
            if not path.startswith(API_PREFIX):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if self.headers.get(SESSION_HEADER) != token:
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    ServiceError(
                        "SESSION_REQUIRED",
                        "本地会话已失效，请刷新 WebUI",
                        403,
                    ).to_dict(),
                )
                return
            try:
                payload = _dispatch_mutation(service, method, path, self._read_json())
            except ServiceError as exc:
                self._send_json(exc.http_status, exc.to_dict())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    ServiceError(
                        "INTERNAL_ERROR",
                        "本地服务处理请求失败，请查看诊断",
                        500,
                    ).to_dict(),
                )
                return
            self._send_json(HTTPStatus.OK, payload)

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ServiceError("INVALID_REQUEST", "请求长度无效") from exc
            if length > MAX_JSON_BODY:
                raise ServiceError("INVALID_REQUEST", "请求内容过大", 413)
            if length == 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ServiceError("INVALID_REQUEST", "请求必须是 JSON") from exc
            if not isinstance(payload, dict):
                raise ServiceError("INVALID_REQUEST", "JSON 请求必须是对象")
            return payload

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
            if target.name == "index.html":
                self.send_header(SESSION_HEADER, token)
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
    if path == f"{API_PREFIX}/accounts/profiles":
        return service.discover_profiles()
    if path == f"{API_PREFIX}/doctor":
        return service.doctor_account()
    if path == f"{API_PREFIX}/system/status":
        return service.system_status()
    if path == f"{API_PREFIX}/tasks":
        return service.list_tasks()
    if path == f"{API_PREFIX}/drafts":
        return service.list_drafts()
    if path == f"{API_PREFIX}/records":
        return service.list_records()

    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) == 5 and parts[:3] == ["api", "v1", "accounts"]:
        account, action = parts[3], parts[4]
        if action == "status":
            return service.get_account_status(account)
        if action == "doctor":
            return service.doctor_account(account)
        if action == "pairing":
            return service.account_pairing_status(account)
        if action == "bridge":
            return service.get_bridge_status(account)
        if action == "autostart":
            return service.get_account_autostart(account)
        if action == "switch":
            return service.get_account_switch(account)
    if len(parts) == 4 and parts[:3] == ["api", "v1", "tasks"]:
        return service.get_task(parts[3])
    raise ServiceError("NOT_FOUND", "接口不存在", 404)


def _dispatch_mutation(
    service: ApplicationService,
    method: str,
    path: str,
    body: dict,
) -> dict:
    if method == "POST" and path == f"{API_PREFIX}/system/pause":
        return service.set_global_pause(True)
    if method == "POST" and path == f"{API_PREFIX}/system/resume":
        return service.set_global_pause(False)
    if method == "POST" and path == f"{API_PREFIX}/diagnostics/export":
        return service.export_diagnostics()
    if method == "POST" and path == f"{API_PREFIX}/system/settings":
        return service.update_system_settings(**body)
    if method == "POST" and path == f"{API_PREFIX}/tasks":
        return service.create_task(**body)
    if method == "POST" and path == f"{API_PREFIX}/drafts":
        return service.create_draft(**body)
    if method == "POST" and path == f"{API_PREFIX}/accounts":
        return service.create_account_slot(**body)
    if method == "POST" and path == f"{API_PREFIX}/accounts/import":
        return service.import_account_slot(**body)
    if method == "POST" and path == f"{API_PREFIX}/accounts/discover":
        return service.discover_profiles(body.get("user_data_dir"))

    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 4
        and parts[:3] == ["api", "v1", "accounts"]
        and method == "DELETE"
    ):
        return service.remove_account_slot(parts[3], **body)
    if (
        len(parts) == 4
        and parts[:3] == ["api", "v1", "drafts"]
        and method == "PATCH"
    ):
        return service.update_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "drafts"]
        and method == "POST"
        and parts[4] == "confirm"
    ):
        return service.confirm_draft(
            parts[3],
            ttl_seconds=int(body.get("ttl_seconds", 300)),
        )
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "drafts"]
        and method == "POST"
        and parts[4] == "execute"
    ):
        return service.execute_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "execute"
    ):
        return service.execute_task(parts[3])
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "retry"
    ):
        return service.retry_task(parts[3])
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "cancel"
    ):
        return service.cancel_task(parts[3])
    if len(parts) == 6 and parts[:3] == ["api", "v1", "accounts"]:
        account, section, action = parts[3], parts[4], parts[5]
        if method == "POST" and (section, action) == ("pairing", "begin"):
            return service.begin_account_pairing(account, **body)
        if method == "POST" and (section, action) == ("setup", "begin"):
            return service.begin_account_setup(account, **body)
        if method == "POST" and (section, action) == ("identity", "check"):
            return service.check_account_identity(account)
        if method == "POST" and (section, action) == ("identity", "record"):
            return service.record_account_identity(account, **body)
        if method == "POST" and (section, action) == ("auth", "logout"):
            return service.logout_account(account, **body)
        if method == "POST" and (section, action) == ("switch", "begin"):
            return service.begin_account_switch(account, **body)
        if method == "POST" and (section, action) == ("switch", "complete"):
            return service.complete_account_switch(account, **body)
        if method == "POST" and (section, action) == ("switch", "cancel"):
            return service.cancel_account_switch(account, **body)
        if method == "POST" and (section, action) == ("bridge", "start"):
            return service.start_account_bridge(account)
        if method == "POST" and (section, action) == ("bridge", "start-only"):
            return service.start_account_bridge_only(account)
        if method == "POST" and (section, action) == ("bridge", "stop"):
            return service.stop_account_bridge(account)
        if method == "POST" and (section, action) == ("bridge", "restart"):
            return service.restart_account_bridge(account)
        if method == "POST" and (section, action) == ("autostart", "update"):
            return service.set_account_autostart(account, **body)
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
    parser = argparse.ArgumentParser(description="启动 auto-xhs 本地 WebUI")
    parser.add_argument("--host", default="127.0.0.1", help="固定为 127.0.0.1")
    parser.add_argument("--port", type=int, default=8765, help="本地 WebUI 端口")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    serve(args.host, args.port)
