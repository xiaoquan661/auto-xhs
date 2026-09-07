"""Loopback-only executor API with an optional local observation console."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlparse

try:
    from .application_service import ApplicationService, ServiceError
    from .web_server import (
        API_PREFIX,
        LocalThreadingHTTPServer,
        _dispatch_api,
        _dispatch_mutation,
    )
    from .web_session import MAX_JSON_BODY, SESSION_HEADER, create_session_token
except ImportError:  # Direct execution: python scripts/executor_server.py
    from application_service import ApplicationService, ServiceError
    from web_server import API_PREFIX, LocalThreadingHTTPServer, _dispatch_api, _dispatch_mutation
    from web_session import MAX_JSON_BODY, SESSION_HEADER, create_session_token


DEFAULT_EXECUTOR_PORT = 8766
WORKER_WEB_ROOT = Path(__file__).resolve().parent.parent / "webui" / "worker"
WORKER_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/worker.css": ("worker.css", "text/css; charset=utf-8"),
    "/worker.js": ("worker.js", "application/javascript; charset=utf-8"),
}


class WorkerSnapshotService(Protocol):
    def dashboard_snapshot(self) -> dict: ...

    def pause_machine(self) -> dict: ...

    def resume_machine(self) -> dict: ...

    def restart_executor_service(self) -> dict: ...

    def account_doctor(self, account: str) -> dict: ...

    def account_action(self, account: str, action: str) -> dict: ...

    def start_account_field_action(self, request_id: str) -> dict: ...

    def complete_account_field_action(self, request_id: str) -> dict: ...


def _validate_bind_host(host: str) -> str:
    if host != "127.0.0.1":
        raise ValueError("执行机执行服务只允许监听 127.0.0.1")
    return host


def make_executor_handler(
    service: ApplicationService,
    *,
    session_token: str | None = None,
    worker_service: WorkerSnapshotService | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Build the executor API and read-only worker console handler."""

    token = session_token or create_session_token()

    class ExecutorHandler(BaseHTTPRequestHandler):
        server_version = "auto-xhs-executor/1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith(API_PREFIX):
                self._handle_api(path)
                return
            self._serve_worker_asset(path)

        def do_POST(self) -> None:
            self._handle_mutation("POST")

        def do_PATCH(self) -> None:
            self._handle_mutation("PATCH")

        def do_DELETE(self) -> None:
            self._handle_mutation("DELETE")

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _handle_api(self, path: str) -> None:
            try:
                if path == f"{API_PREFIX}/worker/snapshot":
                    if worker_service is None:
                        raise ServiceError(
                            "WORKER_CONSOLE_UNAVAILABLE", "员工机状态服务尚未就绪", 503
                        )
                    payload = worker_service.dashboard_snapshot()
                else:
                    payload = (
                        {"success": True, "session_token": token}
                        if path == f"{API_PREFIX}/session"
                        else _dispatch_api(service, path)
                    )
                if path == f"{API_PREFIX}/health":
                    payload = {
                        **payload,
                        "service": "auto-xhs-executor",
                        "mode": "headless_executor",
                    }
            except ServiceError as exc:
                self._send_json(exc.http_status, exc.to_dict())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    ServiceError(
                        "INTERNAL_ERROR",
                        "执行服务处理请求失败，请查看诊断",
                        500,
                    ).to_dict(),
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
                        "执行服务会话已失效，请重新获取会话令牌",
                        403,
                    ).to_dict(),
                )
                return
            try:
                body = self._read_json()
                payload = self._dispatch_worker_mutation(method, path, body)
                if payload is None:
                    payload = _dispatch_mutation(service, method, path, body)
            except ServiceError as exc:
                self._send_json(exc.http_status, exc.to_dict())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    ServiceError(
                        "INTERNAL_ERROR",
                        "执行服务处理请求失败，请查看诊断",
                        500,
                    ).to_dict(),
                )
                return
            self._send_json(HTTPStatus.OK, payload)

        def _dispatch_worker_mutation(
            self,
            method: str,
            path: str,
            _body: dict,
        ) -> dict | None:
            prefix = f"{API_PREFIX}/worker"
            if not path.startswith(f"{prefix}/"):
                return None
            if worker_service is None:
                raise ServiceError(
                    "WORKER_CONSOLE_UNAVAILABLE",
                    "员工机状态服务尚未就绪",
                    503,
                )
            if method == "POST" and path == f"{prefix}/pause":
                return worker_service.pause_machine()
            if method == "POST" and path == f"{prefix}/resume":
                return worker_service.resume_machine()
            if method == "POST" and path == f"{prefix}/executor/restart":
                return worker_service.restart_executor_service()

            parts = [part for part in path.split("/") if part]
            if (
                method == "POST"
                and len(parts) == 6
                and parts[:4] == ["api", "v1", "worker", "account-requests"]
            ):
                request_id = unquote(parts[4])
                if parts[5] == "start":
                    return worker_service.start_account_field_action(request_id)
                if parts[5] == "complete":
                    return worker_service.complete_account_field_action(request_id)
            if (
                method == "POST"
                and len(parts) == 7
                and parts[:4] == ["api", "v1", "worker", "accounts"]
            ):
                account = unquote(parts[4])
                section, action = parts[5], parts[6]
                if (section, action) == ("status", "doctor"):
                    return worker_service.account_doctor(account)
                account_actions = {
                    ("profile", "start"): "profile-start",
                    ("bridge", "restart"): "bridge-restart",
                    ("identity", "check"): "identity-check",
                }
                account_action = account_actions.get((section, action))
                if account_action:
                    return worker_service.account_action(account, account_action)
            raise ServiceError("NOT_FOUND", "员工机控制接口不存在", 404)

        def _serve_worker_asset(self, path: str) -> None:
            asset = WORKER_ASSETS.get(path)
            if asset is None:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            file_name, content_type = asset
            target = WORKER_WEB_ROOT / file_name
            try:
                content = target.read_bytes()
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

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

        def _send_json(self, status: int, payload: dict) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return ExecutorHandler


def serve(host: str = "127.0.0.1", port: int = DEFAULT_EXECUTOR_PORT) -> None:
    try:
        from .manager_worker_service import ManagerWorkerService
    except ImportError:  # Direct execution: python scripts/executor_server.py
        from manager_worker_service import ManagerWorkerService

    service = ApplicationService()
    worker_service = ManagerWorkerService(local_api_url=f"http://127.0.0.1:{port}{API_PREFIX}")
    server = LocalThreadingHTTPServer(
        (_validate_bind_host(host), port),
        make_executor_handler(service, worker_service=worker_service),
    )
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 auto-xhs 执行机执行服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_EXECUTOR_PORT)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
