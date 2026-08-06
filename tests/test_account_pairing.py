from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.account_manager import add_account, load_account
from scripts.account_pairing import (
    consume_pairing_session,
    create_pairing_session,
    decode_pairing_bundle,
    get_pairing_status,
    pairing_session_path,
    revoke_account_pairing,
)
from scripts.cli import cmd_account_onboard


def _make_extension(path: Path) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    (path / "background.js").write_text("// universal", encoding="utf-8")
    (path / "bridge_config.js").write_text("// default", encoding="utf-8")
    return path


def test_pairing_bundle_is_one_time_and_rotates_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    config = add_account("alpha", bridge_port=19601, extension_source=source)
    old_token = config.bridge_token

    pairing = create_pairing_session(config, ttl_seconds=120)
    payload = decode_pairing_bundle(pairing["pairing_bundle"])
    assert payload["account"] == "alpha"
    assert payload["bridgeUrl"] == "ws://localhost:19601"
    assert old_token not in pairing["pairing_bundle"]

    paired = consume_pairing_session(
        "alpha",
        payload["pairingCode"],
        instance_id="instance_alpha_001",
        extension_id="a" * 32,
        profile_directory="Default",
    )
    assert paired.extension_instance_id == "instance_alpha_001"
    assert paired.bridge_token != old_token
    assert not pairing_session_path("alpha").exists()

    with pytest.raises(RuntimeError, match="不存在或已经使用"):
        consume_pairing_session(
            "alpha",
            payload["pairingCode"],
            instance_id="instance_alpha_001",
            extension_id="a" * 32,
            profile_directory="Default",
        )


def test_pairing_rejects_wrong_and_expired_codes(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    config = add_account("alpha", bridge_port=19611, extension_source=source)
    pairing = create_pairing_session(config, ttl_seconds=120)
    payload = decode_pairing_bundle(pairing["pairing_bundle"])

    with pytest.raises(RuntimeError, match="不正确"):
        consume_pairing_session(
            "alpha",
            "wrong-code",
            instance_id="instance_alpha_002",
            extension_id="b" * 32,
            profile_directory="Default",
        )

    session_path = pairing_session_path("alpha")
    session = json.loads(session_path.read_text(encoding="utf-8"))
    session["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    session_path.write_text(json.dumps(session), encoding="utf-8")
    with pytest.raises(RuntimeError, match="已经过期"):
        consume_pairing_session(
            "alpha",
            payload["pairingCode"],
            instance_id="instance_alpha_002",
            extension_id="b" * 32,
            profile_directory="Default",
        )


def test_two_accounts_share_code_but_keep_independent_pairings(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    alpha = add_account("alpha", bridge_port=19621, extension_source=source)
    beta = add_account("beta", bridge_port=19622, extension_source=source)
    assert alpha.extension_dir == beta.extension_dir

    alpha_payload = decode_pairing_bundle(
        create_pairing_session(alpha)["pairing_bundle"]
    )
    beta_payload = decode_pairing_bundle(create_pairing_session(beta)["pairing_bundle"])
    consume_pairing_session(
        "alpha",
        alpha_payload["pairingCode"],
        instance_id="profile_default_001",
        extension_id="c" * 32,
        profile_directory="Default",
    )
    consume_pairing_session(
        "beta",
        beta_payload["pairingCode"],
        instance_id="profile_two_0001",
        extension_id="c" * 32,
        profile_directory="Default",
    )

    assert load_account("alpha").extension_instance_id == "profile_default_001"
    assert load_account("beta").extension_instance_id == "profile_two_0001"
    assert load_account("alpha").bridge_token != load_account("beta").bridge_token


def test_revoke_pairing_rotates_token_and_clears_instance(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    config = add_account("alpha", bridge_port=19631, extension_source=source)
    payload = decode_pairing_bundle(create_pairing_session(config)["pairing_bundle"])
    paired = consume_pairing_session(
        "alpha",
        payload["pairingCode"],
        instance_id="instance_alpha_003",
        extension_id="d" * 32,
        profile_directory="Default",
    )

    revoked = revoke_account_pairing("alpha")
    assert revoked.extension_instance_id is None
    assert revoked.bridge_token != paired.bridge_token
    assert get_pairing_status("alpha")["paired"] is False


def test_account_onboard_creates_slot_starts_bridge_and_copies_bundle(
    tmp_path, monkeypatch, capsys
):
    import argparse

    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    runtime_calls = []
    copied = []
    monkeypatch.setattr(
        "scripts.cli._ensure_bridge_ready",
        lambda url, config: runtime_calls.append((url, config.name)),
    )
    monkeypatch.setattr(
        "scripts.cli._copy_text_to_clipboard",
        lambda text: copied.append(text) or True,
    )

    args = argparse.Namespace(
        name="alpha",
        port=19641,
        confirm=True,
        ttl=120,
        show_bundle=False,
    )
    with pytest.raises(SystemExit) as exc_info:
        cmd_account_onboard(args)

    assert exc_info.value.code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["success"] is True
    assert report["created"] is True
    assert report["pairing_bundle_copied"] is True
    assert "pairing_bundle" not in report
    assert copied[0].startswith("xhs-pair-v1:")
    assert runtime_calls == [("ws://localhost:19641", "alpha")]
    assert "手动打开目标 Chrome Profile" in report["instruction"]
    assert get_pairing_status("alpha")["pairing_pending"] is True
