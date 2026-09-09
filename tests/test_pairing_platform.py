from __future__ import annotations

import pytest
from account_manager import enroll_extension_instance, load_account
from account_pairing import decode_pairing_bundle
from application_service import ApplicationService, ServiceError
from product_store import ProductStore


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(tmp_path / "shared-extension"))
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text("{}", encoding="utf-8")
    (extension / "bridge_config.js").write_text("// test", encoding="utf-8")
    return ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        extension_source=extension,
        bridge_status_reader=lambda _config: {"bridge_running": False},
    )


@pytest.mark.parametrize("mode", ["create", "import"])
def test_rednote_slot_and_pairing_keep_selected_platform(service, tmp_path, mode):
    if mode == "create":
        created = service.create_account_slot(name="red", platform="rednote", confirmed=True)
    else:
        profile = tmp_path / "Chrome" / "Profile 2"
        profile.mkdir(parents=True)
        (profile / "Preferences").write_text("{}", encoding="utf-8")
        created = service.import_account_slot(
            name="red", platform="rednote", user_data_dir=str(profile.parent),
            profile_directory=profile.name, confirmed=True,
        )
    response = service.begin_account_pairing("red", confirmed=True)
    bundle = decode_pairing_bundle(response["pairing"]["pairing_bundle"])
    assert created["account"]["platform"] == "rednote"
    assert load_account("red").platform == "rednote"
    assert response["pairing"]["platform"] == bundle["platform"] == "rednote"
    assert bundle["profileDirectory"] == ("Profile 2" if mode == "import" else "Default")


def test_initial_pairing_corrects_slot_platform_and_running_bridge(service):
    service.create_account_slot(name="fresh", confirmed=True)
    original = load_account("fresh")
    calls = []
    service._bridge_status_reader = lambda _config: {"bridge_running": True}
    service._bridge_stopper = lambda config: calls.append(("stop", config.platform))
    service._pairing_bridge_starter = lambda config: calls.append(("start", config.platform))
    response = service.begin_account_pairing("fresh", platform="rednote", confirmed=True)
    saved = load_account("fresh")
    assert calls == [("stop", "xiaohongshu"), ("start", "rednote")]
    assert saved.platform == "rednote"
    assert saved.chrome_user_data_dir == original.chrome_user_data_dir
    assert saved.account_id == original.account_id
    assert saved.bridge_port == original.bridge_port
    assert decode_pairing_bundle(response["pairing"]["pairing_bundle"])["platform"] == "rednote"
    calls.clear()
    service.begin_account_pairing("fresh", platform="rednote", confirmed=True)
    assert calls == [("start", "rednote")]


@pytest.mark.parametrize("binding", ["extension", "identity"])
def test_bound_platform_is_kept_during_pairing(service, binding):
    service.create_account_slot(name="bound", confirmed=True)
    if binding == "extension":
        enroll_extension_instance(load_account("bound"), "test_instance_bound")
    else:
        service._identity_loader = lambda _account: {"current": {"user_id": "test-user"}}
    with pytest.raises(ServiceError, match="平台已固定"):
        service.begin_account_pairing("bound", platform="rednote", confirmed=True)
    assert load_account("bound").platform == "xiaohongshu"


def test_platform_change_still_requires_pairing_confirmation(service):
    service.create_account_slot(name="fresh", confirmed=True)
    with pytest.raises(ServiceError) as exc:
        service.begin_account_pairing("fresh", platform="rednote", confirmed=False)
    assert exc.value.code == "CONFIRMATION_REQUIRED"
    assert load_account("fresh").platform == "xiaohongshu"
