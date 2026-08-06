from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.application_service import ApplicationService, ServiceError
from scripts.capability_registry import CAPABILITY_POLICIES


def _config(name: str = "alpha") -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        bridge_port=19801,
        bridge_url="ws://localhost:19801",
        schema_version=2,
        profile_mode="existing",
        extension_mode="universal",
        chrome_user_data_dir=r"C:\Chrome\User Data",
        chrome_profile_directory="Profile 2",
        extension_dir=r"C:\XHS\extension",
        account_id="slot-alpha",
        bridge_token="secret-not-public",
        extension_instance_id="instance-alpha",
    )


class StatusPage:
    status: dict | None = None

    def __init__(self, bridge_url: str, account: str) -> None:
        self.bridge_url = bridge_url
        self.account = account

    def get_server_status(self) -> dict | None:
        return self.status


def test_service_lists_shared_capability_and_account_contracts() -> None:
    config = _config()
    service = ApplicationService(account_lister=lambda: [config])

    capabilities = service.list_capabilities()
    accounts = service.list_accounts()

    assert capabilities["summary"]["total"] == len(CAPABILITY_POLICIES)
    assert capabilities["summary"]["enabled_in_v1"] > 0
    assert accounts["accounts"][0]["name"] == "alpha"
    assert "bridge_token" not in accounts["accounts"][0]


def test_account_status_blocks_without_user_hot_session() -> None:
    config = _config()
    StatusPage.status = None
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        page_factory=StatusPage,
        identity_loader=lambda _account: None,
    )

    result = service.get_account_status("alpha")

    assert result["status"] == "BLOCKED"
    assert result["ready"] is False
    assert result["server_running"] is False
    assert "手动打开" not in result["next_action"]


def test_account_status_requires_live_uid_check_after_connection_is_ready() -> None:
    config = _config()
    StatusPage.status = {
        "extension_connected": True,
        "account_id": "slot-alpha",
        "extension": {
            "profile_directory": "Profile 2",
            "instance_id": "instance-alpha",
            "instance_enrolled": True,
            "identity_verified": True,
        },
    }
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        page_factory=StatusPage,
        identity_loader=lambda _account: {
            "current": {
                "user_id": "user-1",
                "nickname": "测试账号",
                "observed_at": "2026-08-06T00:00:00Z",
            }
        },
    )

    result = service.get_account_status("alpha")

    assert result["status"] == "IDENTITY_CHECK_REQUIRED"
    assert result["connection_ready"] is True
    assert result["ready"] is False
    assert result["profile_verified"] is True
    assert result["identity"]["nickname"] == "测试账号"


def test_account_status_uses_stable_not_found_error() -> None:
    def missing(*_args, **_kwargs):
        raise FileNotFoundError("账号 'missing' 尚未配置")

    service = ApplicationService(account_loader=missing)

    with pytest.raises(ServiceError) as exc_info:
        service.get_account_status("missing")

    assert exc_info.value.code == "ACCOUNT_NOT_FOUND"
    assert exc_info.value.http_status == 404


def test_doctor_is_delegated_without_cli_or_http_dependencies() -> None:
    calls = []

    def diagnostic(account, *, page_factory):
        calls.append((account, page_factory))
        return {"success": True, "healthy": True, "ready": False}

    service = ApplicationService(diagnostic=diagnostic, page_factory=StatusPage)

    result = service.doctor_account("alpha")

    assert result["healthy"] is True
    assert calls == [("alpha", StatusPage)]


def test_cli_read_only_account_commands_use_application_service(monkeypatch, capsys) -> None:
    import argparse
    import json

    from scripts.cli import cmd_account_doctor, cmd_account_list, cmd_account_status

    calls = []

    class FakeApplicationService:
        def list_accounts(self):
            calls.append(("list",))
            return {"success": True, "accounts": []}

        def get_account_status(self, account, *, bridge_url=None):
            calls.append(("status", account, bridge_url))
            return {"success": True, "account": {"name": account}}

        def doctor_account(self, account=None):
            calls.append(("doctor", account))
            return {"success": True, "healthy": True, "ready": False}

    monkeypatch.setattr(
        "application_service.ApplicationService",
        FakeApplicationService,
    )

    with pytest.raises(SystemExit) as list_exit:
        cmd_account_list(argparse.Namespace())
    assert list_exit.value.code == 0
    assert json.loads(capsys.readouterr().out)["accounts"] == []

    with pytest.raises(SystemExit) as status_exit:
        cmd_account_status(
            argparse.Namespace(account="alpha", bridge_url="ws://localhost:19901")
        )
    assert status_exit.value.code == 0
    assert json.loads(capsys.readouterr().out)["account"]["name"] == "alpha"

    with pytest.raises(SystemExit) as doctor_exit:
        cmd_account_doctor(argparse.Namespace(name="alpha", require_ready=False))
    assert doctor_exit.value.code == 0
    assert json.loads(capsys.readouterr().out)["healthy"] is True
    assert calls == [
        ("list",),
        ("status", "alpha", "ws://localhost:19901"),
        ("doctor", "alpha"),
    ]
