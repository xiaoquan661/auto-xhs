from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.account_manager import (
    AccountConfig,
    add_account,
    discover_chrome_profiles,
    enroll_extension_instance,
    import_existing_profile,
    initialize_connection_identity,
    list_accounts,
    load_account,
    sync_account_extension,
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
    config_text = (Path(alpha.extension_dir) / "bridge_config.js").read_text(encoding="utf-8")
    assert '"mode": "universal"' in config_text
    assert '"storageKey": "xhsBridgeBinding"' in config_text
    assert alpha.account_id and alpha.account_id not in config_text
    assert alpha.bridge_token and alpha.bridge_token not in config_text


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


def test_existing_profile_launch_uses_profile_directory_without_extension_flags(
    monkeypatch,
):
    from scripts import cli

    launched: list[list[str]] = []
    monkeypatch.setattr(cli.os.path, "exists", lambda path: "Google\\Chrome" in path)
    monkeypatch.setattr("subprocess.Popen", lambda command: launched.append(command))
    config = AccountConfig(
        name="existing-a",
        bridge_port=19481,
        chrome_user_data_dir=r"C:\Chrome\User Data",
        extension_dir=r"C:\XHS\extension",
        chrome_profile_directory="Profile 3",
        profile_mode="existing",
    )

    cli._open_chrome(config)

    command = launched[0]
    assert "--user-data-dir=C:\\Chrome\\User Data" in command
    assert "--profile-directory=Profile 3" in command
    assert not any(arg.startswith("--load-extension=") for arg in command)
    assert not any(arg.startswith("--disable-extensions-except=") for arg in command)


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
