from __future__ import annotations

from types import SimpleNamespace

import pytest
from account_manager import load_account
from application_service import ApplicationService, ServiceError
from product_store import ProductStore


@pytest.fixture
def identity_service(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(tmp_path / "shared-extension"))
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text("{}", encoding="utf-8")
    (extension / "bridge_config.js").write_text("// test", encoding="utf-8")
    observed = {"logged_in": True, "user_id": "red-user", "nickname": "测试账号"}
    service = ApplicationService(
        extension_source=extension,
        product_store=ProductStore(tmp_path / "product"),
        identity_observer=lambda _page: dict(observed),
    )
    service.create_account_slot(name="red", platform="rednote", confirmed=True)
    config = load_account("red")
    service._page_factory = lambda *_args, **_kwargs: SimpleNamespace(
        platform="rednote",
        get_server_status=lambda: {
            "extension_connected": True,
            "account_id": config.account_id,
            "extension": {
                "platform": "rednote", "profile_directory": "Default",
                "instance_id": "test-instance", "instance_enrolled": True,
                "identity_verified": True,
            },
        },
    )
    return service, observed


def test_initial_identity_requires_confirmation_and_matching_uid(identity_service):
    service, _observed = identity_service
    service.check_account_identity("red")
    assert service.get_account_status("red")["status"] == "IDENTITY_REQUIRED"
    with pytest.raises(ServiceError, match="需要明确确认"):
        service.record_account_identity("red", confirmed=False, expected_user_id="red-user")
    result = service.record_account_identity(
        "red", confirmed=True, expected_user_id="red-user",
    )
    assert result["identity"]["current"]["platform"] == "rednote"
    status = service.get_account_status("red")
    assert status["status"] == "READY"
    assert status["identity"]["matches_record"] is True


def test_confirmation_rechecks_the_displayed_uid(identity_service):
    service, observed = identity_service
    service.check_account_identity("red")
    observed["user_id"] = "different-user"
    with pytest.raises(ServiceError) as exc:
        service.record_account_identity("red", confirmed=True, expected_user_id="red-user")
    assert exc.value.code == "IDENTITY_CHANGED"
    assert service.get_account_status("red")["identity"]["recorded"] is False


def test_initial_confirmation_keeps_existing_identity(identity_service):
    service, observed = identity_service
    service.record_account_identity("red", confirmed=True, expected_user_id="red-user")
    observed["user_id"] = "different-user"
    with pytest.raises(ServiceError) as exc:
        service.record_account_identity("red", confirmed=True, expected_user_id="different-user")
    assert exc.value.code == "IDENTITY_ALREADY_BOUND"
    assert service.get_account_status("red")["status"] == "IDENTITY_MISMATCH"
    assert service.get_account_status("red")["identity"]["user_id"] == "red-user"


def test_failed_identity_read_invalidates_previous_ready_result(identity_service):
    service, observed = identity_service
    service.record_account_identity("red", confirmed=True, expected_user_id="red-user")
    assert service.get_account_status("red")["ready"] is True
    observed["error"] = "页面读取超时"
    with pytest.raises(ServiceError) as exc:
        service.check_account_identity("red")
    assert exc.value.code == "IDENTITY_CHECK_FAILED"
    status = service.get_account_status("red")
    assert status["status"] == "IDENTITY_CHECK_REQUIRED"
    assert status["ready"] is False
