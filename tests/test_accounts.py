from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from scripts.account_manager import (
    AccountConfig,
    add_account,
    archive_account,
    discover_chrome_profiles,
    enroll_extension_instance,
    import_existing_profile,
    initialize_connection_identity,
    list_accounts,
    load_account,
    public_config,
    sync_account_extension,
    universal_extension_dir,
)
from scripts.run_lock import RunLock


def _make_extension(path: Path) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    (path / "background.js").write_text("// test", encoding="utf-8")
    (path / "bridge_config.js").write_text("// default", encoding="utf-8")
    return path


def _make_chrome_profile(root: Path, profile_name: str) -> Path:
    root.mkdir()
    profile = root / profile_name
    profile.mkdir()
    (profile / "Preferences").write_text("{}", encoding="utf-8")
    return profile


def test_default_extension_directory_is_current_workspace(monkeypatch):
    monkeypatch.delenv("XHS_UNIVERSAL_EXTENSION_DIR", raising=False)

    assert universal_extension_dir() == Path(__file__).resolve().parents[1] / "extension"


def test_add_accounts_get_independent_profiles_and_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")

    alpha = add_account("alpha", bridge_port=19401, extension_source=source)
    beta = add_account("beta", bridge_port=19402, extension_source=source)

    assert alpha.bridge_url == "ws://localhost:19401"
    assert beta.bridge_url == "ws://localhost:19402"
    assert alpha.chrome_user_data_dir != beta.chrome_user_data_dir
    assert {item.name for item in list_accounts()} == {"alpha", "beta"}
    assert load_account("alpha") == alpha

    assert alpha.extension_dir == beta.extension_dir
    assert alpha.extension_dir == str(source.resolve())
    config_text = (Path(alpha.extension_dir) / "bridge_config.js").read_text(encoding="utf-8")
    assert '"mode": "universal"' in config_text
    assert '"storageKey": "xhsBridgeBinding"' in config_text
    assert alpha.account_id and alpha.account_id not in config_text
    assert alpha.bridge_token and alpha.bridge_token not in config_text


def test_concurrent_account_creation_allocates_distinct_ports(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")

    with ThreadPoolExecutor(max_workers=4) as executor:
        configs = list(
            executor.map(
                lambda name: add_account(name, extension_source=source),
                ("alpha", "beta", "gamma", "delta"),
            )
        )

    assert len({item.bridge_port for item in configs}) == 4
    assert len(list_accounts()) == 4


def test_failed_account_creation_rolls_back_new_slot(tmp_path, monkeypatch):
    from scripts import account_manager

    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    monkeypatch.setattr(
        account_manager,
        "_write_config",
        lambda _config: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        account_manager.add_account("alpha", extension_source=source)

    assert not (tmp_path / "accounts" / "alpha").exists()


def test_duplicate_port_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    add_account("alpha", bridge_port=19411, extension_source=source)

    with pytest.raises(ValueError, match="已被其他账号占用"):
        add_account("beta", bridge_port=19411, extension_source=source)


def test_explicit_port_owned_by_another_process_is_rejected(tmp_path, monkeypatch):
    from scripts import account_manager

    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setattr(account_manager, "_is_port_available", lambda port: False)
    source = _make_extension(tmp_path / "extension-source")

    with pytest.raises(ValueError, match="已被其他进程占用"):
        account_manager.add_account(
            "alpha", bridge_port=19412, extension_source=source
        )


def test_account_locks_do_not_block_each_other(tmp_path):
    alpha = RunLock(str(tmp_path / "alpha" / "run.lock"))
    beta = RunLock(str(tmp_path / "beta" / "run.lock"))
    duplicate_alpha = RunLock(str(tmp_path / "alpha" / "run.lock"))

    assert alpha.acquire(timeout=0.1)
    try:
        assert beta.acquire(timeout=0.1)
        beta.release()
        assert not duplicate_alpha.acquire(timeout=0.1)
    finally:
        alpha.release()


def test_account_file_is_json(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    config = add_account("brand-a", bridge_port=19421, extension_source=source)

    data = json.loads((tmp_path / "accounts" / "brand-a" / "account.json").read_text("utf-8"))
    assert data["name"] == config.name
    assert data["bridge_port"] == 19421


def test_archive_account_removes_slot_but_preserves_profile_and_extension(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "shared-extension")
    user_data = tmp_path / "User Data"
    profile = _make_chrome_profile(user_data, "Profile 2")
    login_marker = profile / "login-state"
    login_marker.write_text("keep", encoding="utf-8")
    import_existing_profile(
        "alpha",
        user_data_dir=user_data,
        profile_directory="Profile 2",
        bridge_port=19422,
        extension_source=source,
    )

    archived = archive_account("alpha")

    assert list_accounts() == []
    assert not (tmp_path / "accounts" / "alpha").exists()
    archive_path = Path(archived["archive_path"])
    assert archive_path.parent == tmp_path / "accounts" / ".archive"
    assert (archive_path / "account.json").is_file()
    assert (archive_path / "slot-archive.json").is_file()
    assert login_marker.read_text(encoding="utf-8") == "keep"
    assert (source / "manifest.json").is_file()
    assert archived["chrome_profile_preserved"] is True
    assert archived["shared_extension_preserved"] is True


def test_sync_extension_preserves_account_route(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    config = add_account("alpha", bridge_port=19431, extension_source=source)
    (source / "background.js").write_text("// updated", encoding="utf-8")

    sync_account_extension(config, extension_source=source)

    extension_dir = Path(config.extension_dir)
    assert (extension_dir / "background.js").read_text("utf-8") == "// updated"
    route = (extension_dir / "bridge_config.js").read_text("utf-8")
    assert '"mode": "universal"' in route
    assert '"storageKey": "xhsBridgeBinding"' in route
    assert "ws://localhost:19431" not in route


def test_explicit_extension_target_still_gets_a_deployed_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    target = tmp_path / "explicit-extension-target"
    monkeypatch.setenv("XHS_UNIVERSAL_EXTENSION_DIR", str(target))
    source = _make_extension(tmp_path / "extension-source")

    config = add_account("alpha", extension_source=source)

    assert config.extension_dir == str(target.resolve())
    assert (target / "manifest.json").is_file()
    assert '"mode": "universal"' in (target / "bridge_config.js").read_text("utf-8")


def test_import_existing_profile_without_copying_it(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    user_data = tmp_path / "User Data"
    profile = _make_chrome_profile(user_data, "Profile 2")
    marker = profile / "existing-login-data"
    marker.write_text("keep", encoding="utf-8")

    config = import_existing_profile(
        "existing-a",
        user_data_dir=user_data,
        profile_directory="Profile 2",
        bridge_port=19441,
        extension_source=source,
    )

    assert config.profile_mode == "existing"
    assert config.chrome_user_data_dir == str(user_data.resolve())
    assert config.chrome_profile_directory == "Profile 2"
    assert marker.read_text("utf-8") == "keep"
    assert Path(config.extension_dir).parent != profile


def test_import_existing_profile_preserves_chrome_display_name(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    user_data = tmp_path / "User Data"
    _make_chrome_profile(user_data, "Profile 2")

    config = import_existing_profile(
        "profile-2",
        user_data_dir=user_data,
        profile_directory="Profile 2",
        profile_display_name="运营主账号",
        bridge_port=19421,
        extension_source=source,
    )

    assert config.profile_display_name == "运营主账号"
    assert public_config(load_account("profile-2"))["profile_display_name"] == "运营主账号"


def test_public_config_tracks_current_chrome_profile_name(tmp_path):
    user_data = tmp_path / "User Data"
    (user_data / "Profile 2").mkdir(parents=True)
    (user_data / "Local State").write_text(
        json.dumps({"profile": {"info_cache": {"Profile 2": {"name": "最新运营名称"}}}}),
        encoding="utf-8",
    )
    config = AccountConfig(
        name="profile-2",
        bridge_port=19422,
        chrome_user_data_dir=str(user_data),
        extension_dir=None,
        chrome_profile_directory="Profile 2",
        profile_display_name="旧名称",
        profile_mode="existing",
    )

    assert public_config(config)["profile_display_name"] == "最新运营名称"


def test_existing_profile_cannot_be_bound_twice(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    user_data = tmp_path / "User Data"
    _make_chrome_profile(user_data, "Default")
    import_existing_profile(
        "alpha",
        user_data_dir=user_data,
        profile_directory="Default",
        bridge_port=19451,
        extension_source=source,
    )

    with pytest.raises(ValueError, match="已绑定到账号 'alpha'"):
        import_existing_profile(
            "beta",
            user_data_dir=user_data,
            profile_directory="Default",
            bridge_port=19452,
            extension_source=source,
        )


def test_existing_account_can_be_rebound_without_deleting_old_profile(
    tmp_path, monkeypatch
):
    from scripts import account_manager

    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    original = add_account("alpha", bridge_port=19453, extension_source=source)
    old_marker = Path(original.chrome_user_data_dir) / "keep-me"
    old_marker.write_text("preserved", encoding="utf-8")
    user_data = tmp_path / "User Data"
    _make_chrome_profile(user_data, "Default")
    monkeypatch.setattr(account_manager, "_is_port_available", lambda port: False)

    rebound = account_manager.import_existing_profile(
        "alpha",
        user_data_dir=user_data,
        profile_directory="Default",
        extension_source=source,
        replace=True,
    )

    assert rebound.profile_mode == "existing"
    assert rebound.bridge_port == 19453
    assert rebound.chrome_user_data_dir == str(user_data.resolve())
    assert rebound.chrome_profile_directory == "Default"
    assert old_marker.read_text(encoding="utf-8") == "preserved"
    assert (tmp_path / "accounts" / "alpha" / "account.previous.json").is_file()


def test_existing_profile_rejects_path_traversal(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    user_data = tmp_path / "User Data"
    _make_chrome_profile(user_data, "Default")

    with pytest.raises(ValueError, match="单个目录名"):
        import_existing_profile(
            "alpha",
            user_data_dir=user_data,
            profile_directory="..\\Default",
            bridge_port=19461,
            extension_source=source,
        )


def test_old_account_config_remains_compatible(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config_dir = tmp_path / "accounts" / "legacy-a"
    config_dir.mkdir(parents=True)
    (config_dir / "account.json").write_text(
        json.dumps(
            {
                "name": "legacy-a",
                "bridge_port": 19471,
                "chrome_user_data_dir": str(config_dir / "chrome-profile"),
                "extension_dir": str(config_dir / "extension"),
            }
        ),
        encoding="utf-8",
    )

    config = load_account("legacy-a")
    assert config.profile_mode == "managed"
    assert config.chrome_profile_directory is None


def test_legacy_account_can_initialize_and_enroll_connection_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    add_account("alpha", bridge_port=19472, extension_source=source)
    config_path = tmp_path / "accounts" / "alpha" / "account.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data.pop("account_id")
    data.pop("bridge_token")
    config_path.write_text(json.dumps(data), encoding="utf-8")

    upgraded = initialize_connection_identity(load_account("alpha"))
    enrolled = enroll_extension_instance(upgraded, "instance_alpha_123")

    assert upgraded.account_id
    assert upgraded.bridge_token
    assert enrolled.extension_instance_id == "instance_alpha_123"
    route = (Path(upgraded.extension_dir) / "bridge_config.js").read_text(encoding="utf-8")
    assert '"mode": "universal"' in route
    assert upgraded.account_id not in route
    assert upgraded.bridge_token not in route


def test_runtime_profile_connection_requires_expected_profile():
    from account_runtime import evaluate_profile_connection

    config = AccountConfig(
        name="existing-a",
        bridge_port=19481,
        chrome_user_data_dir=r"C:\Chrome\User Data",
        extension_dir=r"C:\XHS\extension",
        chrome_profile_directory="Profile 3",
        profile_mode="existing",
    )

    wrong = evaluate_profile_connection(
        config,
        {
            "extension_connected": True,
            "extension": {"profile_directory": "Default"},
        },
    )
    right = evaluate_profile_connection(
        config,
        {
            "extension_connected": True,
            "extension": {"profile_directory": "Profile 3"},
        },
    )

    assert wrong["profile_verified"] is False
    assert wrong["connected_profile_directory"] == "Default"
    assert right["profile_verified"] is True
    assert right["profile_verification_level"] == "legacy_claim"


def test_runtime_profile_connection_uses_enrolled_instance_as_strong_proof():
    from account_runtime import evaluate_profile_connection

    config = AccountConfig(
        name="paired-a",
        bridge_port=19482,
        chrome_user_data_dir=r"C:\Chrome\User Data",
        extension_dir=r"C:\XHS\extension",
        chrome_profile_directory="Profile 3",
        profile_mode="existing",
        extension_instance_id="instance-expected",
    )

    verified = evaluate_profile_connection(
        config,
        {
            "extension_connected": True,
            "extension": {
                "profile_directory": "Profile 3",
                "instance_id": "instance-expected",
                "instance_enrolled": True,
            },
        },
    )
    wrong_instance = evaluate_profile_connection(
        config,
        {
            "extension_connected": True,
            "extension": {
                "profile_directory": "Profile 3",
                "instance_id": "instance-other",
                "instance_enrolled": False,
            },
        },
    )

    assert verified["profile_verified"] is True
    assert verified["profile_verification_level"] == "paired_instance"
    assert wrong_instance["profile_verified"] is False
    assert wrong_instance["profile_verification_level"] == "unverified"


def test_account_start_reports_extension_not_connected_after_profile_launch(
    monkeypatch, capsys
):
    import argparse

    from scripts.cli import cmd_account_start

    config = AccountConfig(
        name="existing-a",
        bridge_port=19483,
        chrome_user_data_dir=r"C:\Chrome\User Data",
        extension_dir=r"C:\XHS\extension",
        chrome_profile_directory="Profile 3",
        profile_mode="existing",
    )

    monkeypatch.setattr("account_manager.load_account", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("account_manager.public_config", lambda _config: {"name": "existing-a"})
    monkeypatch.setattr(
        "account_lifecycle.start_account_runtime",
        lambda *_args, **_kwargs: {
            "ready": False,
            "status": "BLOCKED",
            "bridge_running": True,
            "extension_connected": False,
            "profile_verified": False,
            "error_code": "EXTENSION_NOT_CONNECTED",
            "message": "已打开绑定的 Chrome Profile，但扩展仍未连接",
            "chrome": {"launched": True},
            "chrome_launched": True,
        },
    )

    args = argparse.Namespace(
        account="existing-a",
        bridge_url=None,
        bridge_only=False,
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_account_start(args)

    assert exc_info.value.code == 2
    report = json.loads(capsys.readouterr().out)
    assert report["success"] is False
    assert report["status"] == "BLOCKED"
    assert report["error_code"] == "EXTENSION_NOT_CONNECTED"
    assert report["chrome_launched"] is True
    assert "已打开绑定的 Chrome Profile" in report["message"]


def test_account_start_bridge_only_does_not_require_chrome(monkeypatch, capsys):
    import argparse

    from scripts.cli import cmd_account_start

    config = AccountConfig(
        name="existing-a",
        bridge_port=19484,
        chrome_user_data_dir=r"C:\Chrome\User Data",
        extension_dir=r"C:\XHS\extension",
        chrome_profile_directory="Profile 3",
        profile_mode="existing",
    )
    monkeypatch.setattr("account_manager.load_account", lambda *_args, **_kwargs: config)
    monkeypatch.setattr("account_manager.public_config", lambda _config: {"name": "existing-a"})
    monkeypatch.setattr(
        "scripts.cli._ensure_bridge_ready",
        lambda *_args, **_kwargs: {"bridge_running": True},
    )

    args = argparse.Namespace(
        account="existing-a",
        bridge_url=None,
        bridge_only=True,
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_account_start(args)

    assert exc_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BRIDGE_READY"
    assert report["chrome_managed"] is False


def test_discover_profiles_shows_display_name_and_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    user_data = tmp_path / "User Data"
    _make_chrome_profile(user_data, "Default")
    profile_two = user_data / "Profile 2"
    profile_two.mkdir()
    (profile_two / "Preferences").write_text("{}", encoding="utf-8")
    (user_data / "Local State").write_text(
        json.dumps(
            {
                "profile": {
                    "info_cache": {
                        "Default": {"name": "主账号"},
                        "Profile 2": {"name": "运营账号"},
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    import_existing_profile(
        "ops",
        user_data_dir=user_data,
        profile_directory="Profile 2",
        bridge_port=19491,
        extension_source=source,
    )

    profiles = discover_chrome_profiles(user_data)

    assert [item["profile_directory"] for item in profiles] == ["Default", "Profile 2"]
    assert profiles[0]["display_name"] == "主账号"
    assert profiles[0]["bound_account"] is None
    assert profiles[1]["display_name"] == "运营账号"
    assert profiles[1]["bound_account"] == "ops"
