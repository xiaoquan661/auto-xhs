from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from scripts.application_service import ServiceError
from scripts.web_server import _dispatch_api, _validate_bind_host, make_handler


class FakeService:
    def health(self) -> dict:
        return {"success": True, "status": "ok"}

    def list_capabilities(self) -> dict:
        return {"success": True, "capabilities": [], "summary": {"total": 0}}

    def list_accounts(self) -> dict:
        return {"success": True, "accounts": [{"name": "alpha"}]}

    def doctor_account(self, account=None) -> dict:
        return {"success": True, "account": account, "healthy": True}

    def get_account_status(self, account: str) -> dict:
        if account == "missing":
            raise ServiceError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        return {"success": True, "account": {"name": account}, "status": "BLOCKED"}


def test_web_server_refuses_non_loopback_bind() -> None:
    assert _validate_bind_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(ValueError, match="127.0.0.1"):
        _validate_bind_host("0.0.0.0")


def test_api_dispatch_routes_to_shared_service() -> None:
    service = FakeService()

    assert _dispatch_api(service, "/api/v1/health")["status"] == "ok"
    assert _dispatch_api(service, "/api/v1/accounts")["accounts"][0]["name"] == "alpha"
    assert (
        _dispatch_api(service, "/api/v1/accounts/alpha/status")["account"]["name"]
        == "alpha"
    )
    with pytest.raises(ServiceError) as exc_info:
        _dispatch_api(service, "/api/v1/unknown")
    assert exc_info.value.code == "NOT_FOUND"


def test_http_server_serves_api_static_ui_and_read_only_boundary() -> None:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(FakeService()),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/api/v1/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["status"] == "ok"

        with urlopen(f"{base_url}/", timeout=3) as response:
            assert "浏览器由你掌控" in response.read().decode("utf-8")

        with urlopen(f"{base_url}/styles.css", timeout=3) as response:
            assert ".overview" in response.read().decode("utf-8")

        with urlopen(f"{base_url}/app.js", timeout=3) as response:
            assert "loadDashboard" in response.read().decode("utf-8")

        request = Request(f"{base_url}/api/v1/accounts", method="POST")
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3)
        assert exc_info.value.code == 405
        error = json.loads(exc_info.value.read().decode("utf-8"))
        assert error["error"]["code"] == "READ_ONLY_API"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_server_maps_service_errors_to_json(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(FakeService(), web_root=tmp_path),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/v1/accounts/missing/status"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(url, timeout=3)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 404
        assert payload["error"]["code"] == "ACCOUNT_NOT_FOUND"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
