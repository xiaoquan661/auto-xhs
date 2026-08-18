from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.application_service import ApplicationService, ServiceError
from scripts.business_runner import BusinessRunner
from scripts.capability_registry import CAPABILITY_POLICIES
from scripts.product_store import ProductStore
from scripts.task_service import TaskService
from xhs.errors import CDPError


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
    last_connection: dict | None = None

    def __init__(
        self,
        bridge_url: str,
        account: str,
        account_id: str | None = None,
        bridge_token: str | None = None,
    ) -> None:
        self.bridge_url = bridge_url
        self.account = account
        self.account_id = account_id
        self.bridge_token = bridge_token
        type(self).last_connection = {
            "bridge_url": bridge_url,
            "account": account,
            "account_id": account_id,
            "bridge_token": bridge_token,
        }

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


def test_service_can_start_bridge_without_opening_profile(tmp_path) -> None:
    config = _config()
    calls = []

    def start_bridge_only(item):
        calls.append(item.name)
        return {"bridge_running": True, "registered": True, "pid": 43}

    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        product_store=ProductStore(tmp_path / "product"),
        pairing_bridge_starter=start_bridge_only,
    )

    result = service.start_account_bridge_only("alpha")

    assert result["lifecycle"]["pid"] == 43
    assert calls == ["alpha"]


def test_guided_setup_starts_bridge_without_reopening_unpaired_existing_profile(tmp_path) -> None:
    config = _config()
    config.extension_instance_id = None
    calls = []
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        product_store=ProductStore(tmp_path / "product"),
        pairing_status_reader=lambda _account: {"paired": False, "pairing_pending": False},
        pairing_bridge_starter=lambda item: calls.append(("bridge", item.name)) or {"bridge_running": True},
        pairing_creator=lambda item, **_kwargs: calls.append(("pairing", item.name)) or {
            "account": item.name,
            "pairing_bundle": "xhs-pair-v1:test",
        },
        bridge_starter=lambda _item: (_ for _ in ()).throw(
            AssertionError("unpaired existing Profile must not enter joint Chrome launch")
        ),
    )

    result = service.begin_account_setup("alpha", confirmed=True)

    assert result["setup"]["phase"] == "WAITING_PAIRING"
    assert result["setup"]["pairing"]["pairing_bundle"] == "xhs-pair-v1:test"
    assert calls == [("bridge", "alpha"), ("pairing", "alpha")]


def test_guided_setup_reuses_joint_start_for_enrolled_extension(tmp_path) -> None:
    config = _config()
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        product_store=ProductStore(tmp_path / "product"),
        pairing_status_reader=lambda _account: {"paired": True, "pairing_pending": False},
        bridge_starter=lambda item: {"account": item.name, "ready": True},
    )

    result = service.begin_account_setup("alpha", confirmed=True)

    assert result["setup"]["phase"] == "CONNECTION_READY"
    assert result["setup"]["lifecycle"]["ready"] is True


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
        parameters={"keyword": "露营"},
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
    assert StatusPage.last_connection == {
        "bridge_url": "ws://localhost:19801",
        "account": "alpha",
        "account_id": "slot-alpha",
        "bridge_token": "secret-not-public",
    }


def test_identity_bridge_error_is_not_reported_as_logged_out(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        page_factory=StatusPage,
        identity_observer=lambda _page: {
            "logged_in": False,
            "error": "Bridge 认证失败",
        },
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.check_account_identity("alpha")

    assert exc_info.value.code == "IDENTITY_CHECK_FAILED"
    assert "Bridge 认证失败" in exc_info.value.message


def test_logged_in_identity_without_uid_has_distinct_error(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        page_factory=StatusPage,
        identity_observer=lambda _page: {"logged_in": True, "user_id": ""},
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.check_account_identity("alpha")

    assert exc_info.value.code == "IDENTITY_UID_UNAVAILABLE"


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


def test_service_runs_confirmed_account_switch_flow(tmp_path) -> None:
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
    current_identity = {
        "logged_in": True,
        "user_id": "user-1",
        "nickname": "旧账号",
    }
    switch_state = {"pending": None}
    logout_calls = []

    def begin_switch(account, observed, **values):
        switch_state["pending"] = {
            "account": account,
            "status": "awaiting_login",
            "from": dict(observed),
            "target_user_id": values["target_user_id"],
            "target_label": values["target_label"],
        }
        return switch_state["pending"]

    def complete_switch(account, observed, **values):
        assert account == "alpha"
        assert observed["user_id"] == "user-2"
        assert values["expected_user_id"] == "user-2"
        switch_state["pending"] = None
        return {"event": "login-switched", "to": dict(observed)}

    def logout_account(_page):
        logout_calls.append(True)
        current_identity.update(logged_in=False, user_id="", nickname="")
        return True

    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: config,
        page_factory=StatusPage,
        identity_loader=lambda _account: {
            "current": {
                "user_id": current_identity["user_id"],
                "nickname": current_identity["nickname"],
            }
        },
        switch_loader=lambda _account: switch_state["pending"],
        switch_history_loader=lambda _account, **_kwargs: [],
        identity_observer=lambda _page: dict(current_identity),
        switch_beginner=begin_switch,
        switch_completer=complete_switch,
        account_logout=logout_account,
        product_store=ProductStore(tmp_path / "product"),
    )

    started = service.begin_account_switch(
        "alpha",
        confirmed=True,
        target_user_id="user-2",
        label="新账号",
    )

    assert started["business_tasks_blocked"] is True
    assert started["verified_logged_out"] is True
    assert logout_calls == [True]
    assert service.get_account_status("alpha")["status"] == "SWITCH_PENDING"
    assert service.get_account_switch("alpha")["pending"]["target_label"] == "新账号"

    current_identity.update(logged_in=True, user_id="user-2", nickname="新账号")
    completed = service.complete_account_switch(
        "alpha",
        confirmed=True,
        expected_user_id="user-2",
        label="新账号",
    )

    assert completed["switch"]["event"] == "login-switched"
    assert completed["business_tasks_blocked"] is False


def test_service_runs_confirmed_standalone_logout(tmp_path) -> None:
    logout_calls = []
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        page_factory=StatusPage,
        account_logout=lambda _page: logout_calls.append(True) or True,
        identity_observer=lambda _page: {"logged_in": False, "user_id": ""},
        product_store=ProductStore(tmp_path / "product"),
    )

    result = service.logout_account("alpha", confirmed=True)

    assert result["logged_out"] is True
    assert result["message"] == "已退出登录"
    assert result["verified_logged_out"] is True
    assert logout_calls == [True]


def test_service_rejects_logout_when_identity_is_still_logged_in(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        page_factory=StatusPage,
        account_logout=lambda _page: True,
        identity_observer=lambda _page: {"logged_in": True, "user_id": "user-1"},
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.logout_account("alpha", confirmed=True)

    assert exc_info.value.code == "ACCOUNT_LOGOUT_FAILED"
    assert "仍检测到原账号登录" in exc_info.value.message


def test_service_archives_slot_after_stopping_local_dependencies(tmp_path) -> None:
    calls = []

    class RemovalPage(StatusPage):
        def is_extension_connected(self):
            return True

        def clear_extension_binding(self):
            calls.append("binding")
            return True

    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        page_factory=RemovalPage,
        bridge_status_reader=lambda _config: {
            "bridge_running": True,
            "registered": True,
        },
        bridge_stopper=lambda _config: calls.append("bridge") or {},
        autostart_disabler=lambda account: calls.append(f"autostart:{account}") or {},
        pairing_revoker=lambda account: calls.append(f"pairing:{account}") or _config(),
        account_archiver=lambda account: calls.append(f"archive:{account}") or {
            "archive_id": "alpha-archive",
            "archive_path": str(tmp_path / "archive"),
        },
        product_store=ProductStore(tmp_path / "product"),
    )
    queued = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="待删除",
        parameters={"keyword": "测试"},
    )["task"]

    result = service.remove_account_slot(
        "alpha", confirmed=True, confirmation_name="alpha"
    )

    assert result["archived"] is True
    assert result["cancelled_task_ids"] == [queued["task_id"]]
    assert result["preserved"] == ["Chrome Profile", "小红书登录数据", "共享通用扩展"]
    assert calls == ["binding", "bridge", "autostart:alpha", "pairing:alpha", "archive:alpha"]
    assert service.tasks.get(queued["task_id"])["state"] == "CANCELLED"


def test_service_refuses_slot_removal_for_wrong_name_or_running_task(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        bridge_status_reader=lambda _config: {
            "bridge_running": False,
            "registered": False,
        },
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as mismatch:
        service.remove_account_slot(
            "alpha", confirmed=True, confirmation_name="Alpha"
        )
    assert mismatch.value.code == "CONFIRMATION_MISMATCH"

    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="运行中",
        parameters={"keyword": "测试"},
    )["task"]
    service.tasks.transition(task["task_id"], "RUNNING")
    with pytest.raises(ServiceError) as busy:
        service.remove_account_slot(
            "alpha", confirmed=True, confirmation_name="alpha"
        )
    assert busy.value.code == "ACCOUNT_BUSY"


def test_service_requires_confirmation_for_standalone_logout(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.logout_account("alpha", confirmed=False)

    assert exc_info.value.code == "CONFIRMATION_REQUIRED"


def test_service_rolls_back_switch_when_auto_logout_fails(tmp_path) -> None:
    switch_state = {"pending": None}

    def begin_switch(account, observed, **_values):
        switch_state["pending"] = {
            "account": account,
            "status": "awaiting_login",
            "from": observed,
        }
        return switch_state["pending"]

    def cancel_switch(_account, _observed, **_values):
        switch_state["pending"] = None
        return {"cancelled": True}

    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        identity_observer=lambda _page: {
            "logged_in": True,
            "user_id": "user-1",
            "nickname": "旧账号",
        },
        switch_loader=lambda _account: switch_state["pending"],
        switch_beginner=begin_switch,
        switch_canceller=cancel_switch,
        account_logout=lambda _page: False,
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.begin_account_switch("alpha", confirmed=True)

    assert exc_info.value.code == "ACCOUNT_SWITCH_BEGIN_FAILED"
    assert switch_state["pending"] is None


def test_service_rolls_back_switch_and_reports_bridge_click_error(tmp_path) -> None:
    switch_state = {"pending": None}

    def begin_switch(account, observed, **_values):
        switch_state["pending"] = {
            "account": account,
            "status": "awaiting_login",
            "from": observed,
        }
        return switch_state["pending"]

    def cancel_switch(_account, _observed, **_values):
        switch_state["pending"] = None
        return {"cancelled": True}

    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        identity_observer=lambda _page: {
            "logged_in": True,
            "user_id": "user-1",
            "nickname": "旧账号",
        },
        switch_loader=lambda _account: switch_state["pending"],
        switch_beginner=begin_switch,
        switch_canceller=cancel_switch,
        account_logout=lambda _page: (_ for _ in ()).throw(
            CDPError("Bridge 点击失败")
        ),
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.begin_account_switch("alpha", confirmed=True)

    assert exc_info.value.code == "ACCOUNT_SWITCH_BEGIN_FAILED"
    assert "Bridge 点击失败" in exc_info.value.message
    assert switch_state["pending"] is None


def test_service_requires_confirmation_for_account_switch_mutations(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as begin_error:
        service.begin_account_switch("alpha", confirmed=False)
    with pytest.raises(ServiceError) as complete_error:
        service.complete_account_switch("alpha", confirmed=False)
    with pytest.raises(ServiceError) as cancel_error:
        service.cancel_account_switch("alpha", confirmed=False)

    assert begin_error.value.code == "CONFIRMATION_REQUIRED"
    assert complete_error.value.code == "CONFIRMATION_REQUIRED"
    assert cancel_error.value.code == "CONFIRMATION_REQUIRED"


def test_service_blocks_identity_record_while_switch_is_pending(tmp_path) -> None:
    service = ApplicationService(
        account_loader=lambda *_args, **_kwargs: _config(),
        switch_loader=lambda _account: {"status": "awaiting_login"},
        product_store=ProductStore(tmp_path / "product"),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.record_account_identity("alpha", confirmed=True)

    assert exc_info.value.code == "ACCOUNT_SWITCH_PENDING"


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


def test_service_does_not_report_two_running_tasks_for_one_account(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    executed_accounts = []
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(
            lambda account, _capability, _parameters: executed_accounts.append(account)
            or {"count": 1}
        ),
    )
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": True, "next_action": None},
    )
    first = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="alpha 正在运行",
        parameters={"keyword": "露营"},
    )["task"]
    duplicate = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="alpha 重复任务",
        parameters={"keyword": "美食"},
    )["task"]
    other_account = service.create_task(
        source="webui",
        account_slot="beta",
        capability="search-feeds",
        request_summary="beta 并行任务",
        parameters={"keyword": "旅行"},
    )["task"]
    service.tasks.claim_for_execution(first["task_id"])

    blocked = service.execute_task(duplicate["task_id"])
    parallel = service.execute_task(other_account["task_id"])

    assert blocked["task"]["state"] == "BLOCKED"
    assert blocked["task"]["error_code"] == "ACCOUNT_BUSY"
    assert parallel["task"]["state"] == "SUCCESS"
    assert executed_accounts == ["beta"]


def test_service_rejects_missing_task_parameters(tmp_path) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))

    with pytest.raises(ServiceError) as search_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="search-feeds",
            request_summary="搜索",
            parameters={"keyword": ""},
        )
    with pytest.raises(ServiceError) as interaction_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="like-feed",
            request_summary="点赞",
            parameters={"feed_id": "feed-1", "xsec_token": ""},
        )
    with pytest.raises(ServiceError) as detail_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="get-feed-detail",
            request_summary="查看详情",
            parameters={"xsec_token": "token"},
        )
    with pytest.raises(ServiceError) as profile_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="user-profile",
            request_summary="查看主页",
            parameters={"user_id": "user-1"},
        )
    with pytest.raises(ServiceError) as browse_time_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="browse-feeds",
            request_summary="自动浏览",
            parameters={"duration_minutes": 0, "count": 5},
        )
    with pytest.raises(ServiceError) as browse_count_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="browse-feeds",
            request_summary="自动浏览",
            parameters={"duration_minutes": 5, "count": 51},
        )
    with pytest.raises(ServiceError) as keyword_engagement_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="keyword-engagement",
            request_summary="随机点赞",
            parameters={"keyword": "", "action": "like", "count": 3},
        )
    with pytest.raises(ServiceError) as candidate_pool_error:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="keyword-engagement",
            request_summary="随机点赞",
            parameters={
                "keyword": "露营",
                "action": "like",
                "count": 5,
                "candidate_pool_size": 3,
                "collection_minutes": 2,
            },
        )

    assert search_error.value.code == "INVALID_REQUEST"
    assert interaction_error.value.code == "INVALID_REQUEST"
    assert detail_error.value.code == "INVALID_REQUEST"
    assert profile_error.value.code == "INVALID_REQUEST"
    assert browse_time_error.value.code == "INVALID_REQUEST"
    assert browse_count_error.value.code == "INVALID_REQUEST"
    assert keyword_engagement_error.value.code == "INVALID_REQUEST"
    assert candidate_pool_error.value.code == "INVALID_REQUEST"


def test_keyword_engagement_preserves_item_results_and_partial_state(tmp_path, monkeypatch) -> None:
    received: dict = {}

    def execute(account, capability, parameters):
        received.update(account=account, capability=capability, parameters=parameters)
        return {
            "success": False,
            "partial": True,
            "result_type": "keyword_engagement",
            "message": "随机互动部分完成",
            "items": [{"feed_id": "feed-1", "success": False, "actions": {}}],
        }

    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(execute),
    )
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": True, "next_action": None},
    )
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="keyword-engagement",
        request_summary="关键词随机点赞：露营 / 1 篇",
        parameters={"keyword": "露营", "action": "like", "count": 1},
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "PARTIAL_SUCCESS"
    assert result["result"]["items"][0]["feed_id"] == "feed-1"
    assert received["capability"] == "keyword-engagement"
    assert received["parameters"]["excluded_by_action"] == {"like": [], "favorite": []}


def test_random_comment_requires_click_authorization_and_valid_limits(tmp_path) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))

    with pytest.raises(ServiceError) as missing_authorization:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="random-comment",
            request_summary="随机评论",
            parameters={"count": 1, "candidate_pool_size": 20, "collection_minutes": 2},
        )
    with pytest.raises(ServiceError) as excessive_count:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="random-comment",
            request_summary="随机评论",
            parameters={
                "count": 4,
                "candidate_pool_size": 20,
                "collection_minutes": 2,
                "style": "natural",
                "direct_send_authorized": True,
            },
        )

    assert missing_authorization.value.code == "CONFIRMATION_REQUIRED"
    assert excessive_count.value.code == "INVALID_REQUEST"


def test_random_comment_executes_immediately_and_preserves_item_results(tmp_path, monkeypatch) -> None:
    received: dict = {}

    def execute(account, capability, parameters):
        received.update(account=account, capability=capability, parameters=parameters)
        return {
            "success": True,
            "partial": False,
            "result_type": "random_comment",
            "message": "随机评论完成，共发送 1 条评论",
            "items": [
                {
                    "feed_id": "feed-1",
                    "title": "露营清单",
                    "content": "整理得很清楚",
                    "status": "success",
                    "success": True,
                }
            ],
        }

    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(execute),
    )
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": True, "next_action": None},
    )
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="random-comment",
        request_summary="首页随机评论：直接发送 1 条",
        parameters={
            "count": 1,
            "candidate_pool_size": 20,
            "collection_minutes": 2,
            "style": "natural",
            "direct_send_authorized": True,
        },
    )["task"]

    result = service.execute_task(task["task_id"])

    assert task["state"] == "QUEUED"
    assert result["task"]["state"] == "SUCCESS"
    assert result["result"]["items"][0]["content"] == "整理得很清楚"
    assert received["capability"] == "random-comment"
    assert received["parameters"]["direct_send_authorized"] is True


def test_unexpected_l0_failure_reaches_failed_terminal_state(tmp_path, monkeypatch) -> None:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(
            lambda _account, _capability, _parameters: (_ for _ in ()).throw(
                RuntimeError("页面解析失败")
            )
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
        capability="list-feeds",
        request_summary="浏览首页",
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "FAILED"
    assert result["task"]["error_code"] == "EXECUTION_ERROR"


def test_service_recovers_interrupted_task_on_startup(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    task = TaskService(store).create(
        source="webui",
        account_slot="alpha",
        capability="list-feeds",
        request_summary="浏览首页",
    )
    TaskService(store).transition(task["task_id"], "RUNNING")

    service = ApplicationService(product_store=store)

    assert service.get_task(task["task_id"])["task"]["state"] == "FAILED"


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
    records = service.list_records()["records"]
    assert records[0]["state"] == "BLOCKED"
    assert records[0]["error_code"] == "ACCOUNT_NOT_READY"


def test_service_retries_blocked_task_after_account_recovers(tmp_path, monkeypatch) -> None:
    readiness = iter(
        [
            {"ready": False, "next_action": "手动打开 Chrome"},
            {"ready": True, "next_action": None},
        ]
    )
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(
            lambda _account, _capability, _parameters: {"count": 3}
        ),
    )
    monkeypatch.setattr(service, "get_account_status", lambda _account: next(readiness))
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="list-feeds",
        request_summary="浏览首页",
    )["task"]
    service.execute_task(task["task_id"])

    result = service.retry_task(task["task_id"])

    assert result["task"]["state"] == "SUCCESS"
    assert result["task"]["result_summary"] == "完成，共 3 条结果"
    assert [item["state"] for item in service.list_records()["records"]] == [
        "SUCCESS",
        "BLOCKED",
    ]


def test_service_cancels_blocked_task_and_records_it(tmp_path, monkeypatch) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": False, "next_action": "启动 Bridge"},
    )
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="list-feeds",
        request_summary="浏览首页",
    )["task"]
    service.execute_task(task["task_id"])

    result = service.cancel_task(task["task_id"])

    assert result["task"]["state"] == "CANCELLED"
    assert service.list_records()["records"][0]["state"] == "CANCELLED"


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


def test_l1_quota_rejection_becomes_visible_blocked_task(tmp_path, monkeypatch) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda _account: {"ready": True, "next_action": None},
    )

    def reject(**_kwargs):
        raise ServiceError("QUOTA_EXCEEDED", "已达到当前操作配额", 409)

    monkeypatch.setattr(service.quota, "check_l1", reject)
    task = service.create_task(
        source="webui",
        account_slot="alpha",
        capability="like-feed",
        request_summary="点赞",
        parameters={"feed_id": "feed-1", "xsec_token": "token"},
    )["task"]

    result = service.execute_task(task["task_id"])

    assert result["task"]["state"] == "BLOCKED"
    assert result["task"]["error_code"] == "QUOTA_EXCEEDED"
    assert result["task"]["recommended_action"] == "已达到当前操作配额"


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
    assert service.list_drafts()["drafts"][0]["status"] == "EXECUTED"
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
    assert service.list_drafts()["drafts"][0]["status"] == "RESULT_UNKNOWN"


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
