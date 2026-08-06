from __future__ import annotations

import json
from pathlib import Path

from scripts import account_doctor
from scripts.account_manager import add_account, import_existing_profile


def _make_extension(path: Path) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    (path / "bridge_config.js").write_text("// default", encoding="utf-8")
    return path


def _make_chrome_profile(root: Path, name: str = "Default") -> Path:
    root.mkdir()
    profile = root / name
    profile.mkdir()
    (profile / "Preferences").write_text("{}", encoding="utf-8")
    return profile


class ReadyPage:
    def __init__(self, bridge_url: str, account: str):
        self.bridge_url = bridge_url
        self.account = account

    def is_server_running(self) -> bool:
        return True

    def is_extension_connected(self) -> bool:
        return True


class StoppedPage(ReadyPage):
    def is_server_running(self) -> bool:
        return False

    def is_extension_connected(self) -> bool:
        return False


class WrongProfilePage(ReadyPage):
    def get_server_status(self) -> dict:
        return {
            "extension_connected": True,
            "extension": {"profile_directory": "Profile 9"},
        }


def test_doctor_reports_two_ready_accounts(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    add_account("alpha", bridge_port=19501, extension_source=source)
    add_account("beta", bridge_port=19502, extension_source=source)

    result = account_doctor.diagnose_accounts(page_factory=ReadyPage)

    assert result["healthy"] is True
    assert result["ready"] is True
    assert result["summary"] == {
        "total_accounts": 2,
        "healthy_accounts": 2,
        "ready_accounts": 2,
        "errors": 0,
        "warnings": 6,
        "info": 2,
    }


def test_doctor_detects_duplicate_port_and_bad_route(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    add_account("alpha", bridge_port=19511, extension_source=source)
    beta = add_account("beta", bridge_port=19512, extension_source=source)
    beta_config = tmp_path / "accounts" / "beta" / "account.json"
    data = json.loads(beta_config.read_text(encoding="utf-8"))
    data["bridge_port"] = 19511
    beta_config.write_text(json.dumps(data), encoding="utf-8")
    route_path = Path(beta.extension_dir) / "bridge_config.js"
    route_path.write_text(
        'globalThis.XHS_BRIDGE_CONFIG = Object.freeze('
        '{"account":"wrong","bridgeUrl":"ws://localhost:9999"});\n',
        encoding="utf-8",
    )

    result = account_doctor.diagnose_accounts(page_factory=ReadyPage)

    assert result["healthy"] is False
    assert result["summary"]["errors"] >= 3
    beta_report = next(item for item in result["accounts"] if item["name"] == "beta")
    failed = {item["name"] for item in beta_report["checks"] if item["status"] == "fail"}
    assert {"bridge_port_unique", "extension_route"} <= failed


def test_doctor_detects_missing_existing_profile_preferences(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    user_data = tmp_path / "User Data"
    profile = _make_chrome_profile(user_data)
    import_existing_profile(
        "existing",
        user_data_dir=user_data,
        profile_directory="Default",
        bridge_port=19521,
        extension_source=source,
    )
    (profile / "Preferences").unlink()

    result = account_doctor.diagnose_accounts(
        "existing", page_factory=ReadyPage
    )

    assert result["healthy"] is False
    checks = result["accounts"][0]["checks"]
    assert any(
        item["name"] == "profile" and item["status"] == "fail"
        for item in checks
    )


def test_doctor_treats_stopped_runtime_as_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setattr(account_doctor, "_is_port_available", lambda port: True)
    source = _make_extension(tmp_path / "extension-source")
    add_account("alpha", bridge_port=19531, extension_source=source)

    result = account_doctor.diagnose_accounts("alpha", page_factory=StoppedPage)

    assert result["healthy"] is True
    assert result["ready"] is False
    checks = result["accounts"][0]["checks"]
    assert any(
        item["name"] == "bridge_running" and item["status"] == "warning"
        for item in checks
    )


def test_doctor_rejects_connected_extension_from_wrong_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    add_account("alpha", bridge_port=19532, extension_source=source)

    result = account_doctor.diagnose_accounts(
        "alpha", page_factory=WrongProfilePage
    )

    assert result["healthy"] is False
    assert result["ready"] is False
    checks = result["accounts"][0]["checks"]
    assert any(
        item["name"] == "connected_profile" and item["status"] == "fail"
        for item in checks
    )


def test_doctor_reports_corrupt_account_config(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config_dir = tmp_path / "accounts" / "broken"
    config_dir.mkdir(parents=True)
    (config_dir / "account.json").write_text("{broken", encoding="utf-8")

    result = account_doctor.diagnose_accounts(page_factory=ReadyPage)

    assert result["healthy"] is False
    assert result["summary"]["total_accounts"] == 1
    assert result["accounts"][0]["name"] == "broken"
    assert result["accounts"][0]["checks"][0]["status"] == "fail"


def test_doctor_keeps_legacy_connection_config_healthy_with_upgrade_warning(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    add_account("legacy", bridge_port=19541, extension_source=source)
    config_path = tmp_path / "accounts" / "legacy" / "account.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    data.pop("account_id")
    data.pop("bridge_token")
    data["schema_version"] = 1
    data["extension_mode"] = "per_account"
    legacy_extension = tmp_path / "accounts" / "legacy" / "extension"
    legacy_extension.mkdir()
    data["extension_dir"] = str(legacy_extension)
    config_path.write_text(json.dumps(data), encoding="utf-8")
    (legacy_extension / "manifest.json").write_text("{}", encoding="utf-8")
    (legacy_extension / "bridge_config.js").write_text(
        'globalThis.XHS_BRIDGE_CONFIG = Object.freeze('
        '{"account":"legacy","bridgeUrl":"ws://localhost:19541"});\n',
        encoding="utf-8",
    )

    result = account_doctor.diagnose_accounts("legacy", page_factory=ReadyPage)

    assert result["healthy"] is True
    checks = result["accounts"][0]["checks"]
    assert any(
        item["name"] == "connection_credentials" and item["status"] == "warning"
        for item in checks
    )
