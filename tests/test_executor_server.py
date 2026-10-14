from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest
from project_paths import project_root, webui_asset_path

from scripts.executor_server import _validate_bind_host, make_executor_handler
from scripts.web_session import SESSION_HEADER


class FakeExecutorService:
    def __init__(self) -> None:
        self.paused = False

    def health(self) -> dict:
        return {"success": True, "service": "auto-xhs-local", "status": "ok"}

    def set_global_pause(self, paused: bool) -> dict:
        self.paused = paused
        return {"success": True, "global_paused": paused}


class FakeWorkerService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def dashboard_snapshot(self) -> dict:
        return {
            "success": True,
            "machine": {"machine_id": "PC055", "status": "READY"},
            "executions": [],
            "agent_messages": [],
            "outbox_records": [],
        }

    def pause_machine(self) -> dict:
        self.calls.append(("pause",))
        return {"success": True, "machine": {"paused": True}}

    def resume_machine(self) -> dict:
        self.calls.append(("resume",))
        return {"success": True, "machine": {"paused": False}}

    def restart_executor_service(self) -> dict:
        self.calls.append(("restart",))
        return {"success": True, "status": "RUNNING"}

    def account_doctor(self, account: str) -> dict:
        self.calls.append(("doctor", account))
        return {"success": True, "account": {"name": account}}

    def account_action(self, account: str, action: str) -> dict:
        self.calls.append(("account-action", account, action))
        return {"success": True, "account": {"name": account}, "action": action}

    def start_account_field_action(self, request_id: str) -> dict:
        self.calls.append(("field-start", request_id))
        return {"success": True, "field_request": {"request_id": request_id}}

    def complete_account_field_action(self, request_id: str) -> dict:
        self.calls.append(("field-complete", request_id))
        return {"success": True, "field_request": {"request_id": request_id}}


def test_executor_server_refuses_non_loopback_bind() -> None:
    assert _validate_bind_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
        _validate_bind_host("0.0.0.0")


def test_executor_handler_exposes_api_and_local_worker_console() -> None:
    token = "executor-session"
    service = FakeExecutorService()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_executor_handler(
            service,  # type: ignore[arg-type]
            session_token=token,
            worker_service=FakeWorkerService(),  # type: ignore[arg-type]
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/api/v1/health", timeout=3) as response:
            health = json.loads(response.read().decode("utf-8"))
        assert health["status"] == "ok"
        assert health["service"] == "auto-xhs-executor"
        assert health["mode"] == "headless_executor"

        with urlopen(f"{base_url}/api/v1/session", timeout=3) as response:
            session = json.loads(response.read().decode("utf-8"))
        assert session["session_token"] == token

        with urlopen(f"{base_url}/", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert "员工机执行台" in page
        assert "不提供任务创建和重复执行" in page

        for name in ("worker.css", "worker.js", "favicon.svg", "favicon-16.png", "favicon-32.png"):
            with urlopen(f"{base_url}/{name}", timeout=3) as response:
                assert response.read() == webui_asset_path(f"worker/{name}").read_bytes()

        with urlopen(f"{base_url}/api/v1/worker/snapshot", timeout=3) as response:
            snapshot = json.loads(response.read().decode("utf-8"))
        assert snapshot["machine"]["machine_id"] == "PC055"

        request = Request(
            f"{base_url}/api/v1/system/pause",
            data=b"{}",
            method="POST",
            headers={SESSION_HEADER: token, "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=3) as response:
            paused = json.loads(response.read().decode("utf-8"))
        assert paused["global_paused"] is True
        assert service.paused is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_worker_console_actions_are_session_protected_and_account_scoped() -> None:
    token = "executor-session"
    worker = FakeWorkerService()
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_executor_handler(
            FakeExecutorService(),  # type: ignore[arg-type]
            session_token=token,
            worker_service=worker,
        ),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    def post(path: str, *, authenticated: bool = True) -> dict:
        headers = {"Content-Type": "application/json"}
        if authenticated:
            headers[SESSION_HEADER] = token
        request = Request(
            f"{base_url}{path}",
            data=b"{}",
            method="POST",
            headers=headers,
        )
        with urlopen(request, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))

    try:
        with pytest.raises(HTTPError) as exc_info:
            post("/api/v1/worker/pause", authenticated=False)
        assert exc_info.value.code == 403
        rejected = json.loads(exc_info.value.read().decode("utf-8"))
        assert rejected["error"]["code"] == "SESSION_REQUIRED"
        assert worker.calls == []

        assert post("/api/v1/worker/pause")["machine"]["paused"] is True
        assert post("/api/v1/worker/resume")["machine"]["paused"] is False
        assert post("/api/v1/worker/executor/restart")["status"] == "RUNNING"
        account = quote("品牌 账号", safe="")
        assert post(f"/api/v1/worker/accounts/{account}/status/doctor")["success"] is True
        assert post(f"/api/v1/worker/accounts/{account}/profile/start")["action"] == "profile-start"
        bridge = post(f"/api/v1/worker/accounts/{account}/bridge/restart")
        identity = post(f"/api/v1/worker/accounts/{account}/identity/check")
        assert bridge["action"] == "bridge-restart"
        assert identity["action"] == "identity-check"
        assert post("/api/v1/worker/account-requests/ACCOUNT-001/start")["success"] is True
        assert post("/api/v1/worker/account-requests/ACCOUNT-001/complete")["success"] is True
        assert worker.calls == [
            ("pause",),
            ("resume",),
            ("restart",),
            ("doctor", "品牌 账号"),
            ("account-action", "品牌 账号", "profile-start"),
            ("account-action", "品牌 账号", "bridge-restart"),
            ("account-action", "品牌 账号", "identity-check"),
            ("field-start", "ACCOUNT-001"),
            ("field-complete", "ACCOUNT-001"),
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_executor_imports_local_routes_without_manager_runtime() -> None:
    probe = (
        "import json, sys; sys.path.insert(0, sys.argv[1]); import executor_server; "
        "print(json.dumps([name for name in sys.modules "
        "if name in {'web_server', 'manager_service', 'manager_agent_service'}]))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe, str(project_root() / "scripts")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    assert json.loads(completed.stdout) == []
