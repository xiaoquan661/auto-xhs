from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.application_service import ApplicationService, ServiceError
from scripts.business_runner import BusinessRunner
from scripts.capability_registry import CAPABILITY_POLICIES
from scripts.product_store import ProductStore


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


def test_service_rejects_v1_disabled_capability() -> None:
    service = ApplicationService()

    with pytest.raises(ServiceError) as exc_info:
        service.require_enabled_capability("publish")

    assert exc_info.value.code == "CAPABILITY_DISABLED"
    assert exc_info.value.http_status == 409


def test_service_resolves_identity_check_without_l3_confirmation() -> None:
    service = ApplicationService()

    policy = service.require_enabled_capability(
        "account-identity",
        operation="check",
    )

    assert policy.risk_level == "L0"
    assert policy.requires_confirmation is False


def test_service_rejects_unknown_capability() -> None:
    service = ApplicationService()

    with pytest.raises(ServiceError) as exc_info:
        service.require_enabled_capability("send-private-message")

    assert exc_info.value.code == "CAPABILITY_NOT_FOUND"


def test_service_global_pause_blocks_new_tasks(tmp_path) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))

    assert service.system_status()["global_paused"] is False
    service.set_global_pause(True)

    with pytest.raises(ServiceError) as exc_info:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="search-feeds",
            request_summary="搜索露营",
        )
    assert exc_info.value.code == "GLOBAL_PAUSED"


def test_service_updates_runtime_settings_after_confirmation(tmp_path) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))

    result = service.update_system_settings(
        confirmed=True,
        global_concurrency=2,
        l1_limits={
            "hourly": 8,
            "daily": 40,
            "dedup_minutes": 15,
            "failure_threshold": 4,
        },
    )

    assert result["global_concurrency"] == 2
    assert result["l1_limits"]["hourly"] == 8
    assert service.runner.max_concurrency == 2


def test_service_bridge_lifecycle_uses_registered_manager(tmp_path) -> None:
    config = _config()
    calls = []

    def start(item):
        calls.append(item.name)
        return {"bridge_running": True, "registered": True, "pid": 42}

    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        product_store=ProductStore(tmp_path / "product"),
        bridge_starter=start,
    )

    result = service.start_account_bridge("alpha")

    assert result["lifecycle"]["pid"] == 42
    assert calls == ["alpha"]


def test_service_exports_diagnostics_to_product_root(tmp_path) -> None:
    target = tmp_path / "report.json"

    def exporter(root, *, diagnosis, system, version):
        assert root == (tmp_path / "product").resolve()
        assert diagnosis["healthy"] is True
        assert system["product_version"]
        assert version
        target.write_text("{}", encoding="utf-8")
        return target

    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        diagnostic=lambda *_args, **_kwargs: {"success": True, "healthy": True},
        diagnostic_exporter=exporter,
    )

    result = service.export_diagnostics()

    assert result["path"] == str(target)


def test_service_creates_task_and_draft_through_shared_boundary(tmp_path) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))

    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="搜索露营",
    )["task"]
    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="uid-alpha",
        action_type="reply-comment",
        target_id="comment-1",
        target_summary="回复评论",
        content="谢谢你的留言",
    )["draft"]

    assert service.list_tasks()["tasks"][0]["task_id"] == task["task_id"]
    assert draft["action_type"] == "reply-comment"


def test_service_creates_slot_and_pairing_in_test_home(tmp_path, monkeypatch) -> None:
    accounts_home = tmp_path / "accounts"
    extension_source = tmp_path / "extension"
    extension_source.mkdir()
    (extension_source / "manifest.json").write_text("{}", encoding="utf-8")
    (extension_source / "bridge_config.js").write_text("// test", encoding="utf-8")
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(accounts_home))
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(tmp_path / "shared-extension"))
    service = ApplicationService(extension_source=extension_source)

    created = service.create_account_slot(name="alpha", confirmed=True)
    pairing = service.begin_account_pairing("alpha", confirmed=True)

    assert created["account"]["name"] == "alpha"
    assert pairing["pairing"]["account"] == "alpha"
    assert pairing["pairing"]["pairing_bundle"].startswith("xhs-pair-v1:")
    assert accounts_home.joinpath("alpha", "account.json").exists()


def test_service_discovers_and_imports_existing_profile(tmp_path, monkeypatch) -> None:
    accounts_home = tmp_path / "accounts"
    extension_source = tmp_path / "extension"
    extension_source.mkdir()
    (extension_source / "manifest.json").write_text("{}", encoding="utf-8")
    (extension_source / "bridge_config.js").write_text("// test", encoding="utf-8")
    user_data = tmp_path / "User Data"
    profile = user_data / "Profile 2"
    profile.mkdir(parents=True)
    (profile / "Preferences").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(accounts_home))
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(tmp_path / "shared-extension"))
    service = ApplicationService(extension_source=extension_source)

    discovered = service.discover_profiles(str(user_data))
    imported = service.import_account_slot(
        name="brand-a",
        user_data_dir=str(user_data),
        profile_directory="Profile 2",
        confirmed=True,
    )

    assert discovered["profiles"][0]["profile_directory"] == "Profile 2"
    assert imported["account"]["profile_mode"] == "existing"
    assert imported["account"]["chrome_profile_directory"] == "Profile 2"


def test_service_requires_explicit_confirmation_for_slot_changes(tmp_path) -> None:
    service = ApplicationService(extension_source=tmp_path)

    with pytest.raises(ServiceError) as exc_info:
        service.create_account_slot(name="alpha", confirmed=False)

    assert exc_info.value.code == "CONFIRMATION_REQUIRED"


def test_account_reaches_ready_only_after_matching_live_uid_check(tmp_path) -> None:
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
    store = ProductStore(tmp_path / "product")
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        page_factory=StatusPage,
        identity_loader=lambda _account: {
            "current": {"user_id": "user-1", "nickname": "测试账号"}
        },
        identity_observer=lambda _page: {
            "logged_in": True,
            "user_id": "user-1",
            "nickname": "测试账号",
            "observed_at": "2026-08-13T00:00:00Z",
        },
        product_store=store,
    )

    assert service.get_account_status("alpha")["status"] == "IDENTITY_CHECK_REQUIRED"
    service.check_account_identity("alpha")
    result = service.get_account_status("alpha")

    assert result["status"] == "READY"
    assert result["ready"] is True


def test_account_identity_mismatch_never_reaches_ready(tmp_path) -> None:
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
        identity_loader=lambda _account: {"current": {"user_id": "recorded-user"}},
        identity_observer=lambda _page: {"logged_in": True, "user_id": "other-user"},
        product_store=ProductStore(tmp_path / "product"),
    )

    service.check_account_identity("alpha")
    result = service.get_account_status("alpha")

    assert result["status"] == "IDENTITY_MISMATCH"
    assert result["ready"] is False


def test_service_executes_ready_l0_task_and_records_result(tmp_path, monkeypatch) -> None:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(
            lambda account, capability, parameters: {
                "count": 2,
                "account": account,
                "capability": capability,
                "keyword": parameters["keyword"],
            }
        ),
    )
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": True, "next_action": None},
    )
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="搜索露营",
        parameters={"keyword": "露营"},
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "SUCCESS"
    assert result["result"]["count"] == 2
    assert service.list_records()["records"][0]["task_id"] == task["task_id"]


def test_service_blocks_task_when_account_is_not_ready(tmp_path, monkeypatch) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": False, "next_action": "手动打开 Chrome"},
    )
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="list-feeds",
        request_summary="浏览首页",
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "BLOCKED"
    assert result["task"]["recommended_action"] == "手动打开 Chrome"


def test_l1_uncertain_failure_never_retries_automatically(tmp_path, monkeypatch) -> None:
    def uncertain(_account, _capability, _parameters):
        raise ServiceError("ACTION_FAILED", "未能确认状态", 409)

    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(uncertain),
    )
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": True, "next_action": None},
    )
    task = service.create_task(
        source="codex",
        account_slot="alpha",
        capability="like-feed",
        request_summary="点赞",
        parameters={"feed_id": "feed-1", "xsec_token": "token"},
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "RESULT_UNKNOWN"
    assert "回读" in result["task"]["recommended_action"]


def _ready_output_service(tmp_path, executor):
    store = ProductStore(tmp_path / "product")
    service = ApplicationService(
        product_store=store,
        business_runner=BusinessRunner(executor),
    )
    service.get_account_status = lambda _account: {
        "ready": True,
        "next_action": None,
        "identity": {"live_user_id": "uid-alpha"},
    }
    return service


def test_confirmed_comment_executes_once_and_records_result(tmp_path) -> None:
    calls = []

    def executor(account, capability, parameters):
        calls.append((account, capability, parameters))
        return {"success": True, "message": "评论发送成功"}

    service = _ready_output_service(tmp_path, executor)
    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="uid-alpha",
        action_type="post-comment",
        target_id="feed-1",
        target_summary="露营笔记",
        content="很实用",
    )["draft"]
    approval = service.confirm_draft(draft["draft_id"])["approval"]

    result = service.execute_draft(
        draft["draft_id"],
        approval_id=approval["approval_id"],
        feed_id="feed-1",
        xsec_token="token",
    )

    assert result["task"]["state"] == "SUCCESS"
    assert calls[0][1] == "post-comment"
    assert calls[0][2]["content"] == "很实用"
    assert service.list_records()["records"][0]["state"] == "SUCCESS"
    with pytest.raises(ServiceError) as consumed:
        service.execute_draft(
            draft["draft_id"],
            approval_id=approval["approval_id"],
            feed_id="feed-1",
            xsec_token="token",
        )
    assert consumed.value.code == "CONFIRMATION_CONSUMED"


def test_comment_execution_rejects_changed_target_or_uid(tmp_path) -> None:
    service = _ready_output_service(tmp_path, lambda *_args: {"success": True})
    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="uid-alpha",
        action_type="post-comment",
        target_id="feed-1",
        target_summary="目标",
        content="内容",
    )["draft"]
    approval = service.confirm_draft(draft["draft_id"])["approval"]

    with pytest.raises(ServiceError) as target_mismatch:
        service.execute_draft(
            draft["draft_id"],
            approval_id=approval["approval_id"],
            feed_id="feed-2",
            xsec_token="token",
        )
    assert target_mismatch.value.code == "CONFIRMATION_MISMATCH"

    service.get_account_status = lambda _account: {
        "ready": True,
        "next_action": None,
        "identity": {"live_user_id": "other-uid"},
    }
    with pytest.raises(ServiceError) as uid_mismatch:
        service.execute_draft(
            draft["draft_id"],
            approval_id=approval["approval_id"],
            feed_id="feed-1",
            xsec_token="token",
        )
    assert uid_mismatch.value.code == "CONFIRMATION_MISMATCH"


def test_comment_adapter_failure_becomes_result_unknown(tmp_path) -> None:
    def fail(*_args):
        raise RuntimeError("页面超时")

    service = _ready_output_service(tmp_path, fail)
    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="uid-alpha",
        action_type="post-comment",
        target_id="feed-1",
        target_summary="目标",
        content="内容",
    )["draft"]
    approval = service.confirm_draft(draft["draft_id"])["approval"]

    result = service.execute_draft(
        draft["draft_id"],
        approval_id=approval["approval_id"],
        feed_id="feed-1",
        xsec_token="token",
    )

    assert result["task"]["state"] == "RESULT_UNKNOWN"
    assert result["task"]["error_code"] == "EXECUTION_ERROR"
    assert "不要直接重发" in result["task"]["recommended_action"]


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
