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


def _make_extension(path: Path) -> Path:
    path.mkdir()
    (path / "manifest.json").write_text("{}", encoding="utf-8")
    (path / "background.js").write_text("// universal", encoding="utf-8")
    (path / "bridge_config.js").write_text("// default", encoding="utf-8")
    return path


def test_pairing_bundle_is_single_use_and_rotates_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = add_account(
        "alpha",
        bridge_port=19601,
        extension_source=_make_extension(tmp_path / "extension-source"),
    )
    old_token = config.bridge_token

    pairing = create_pairing_session(config)
    payload = decode_pairing_bundle(pairing["pairing_bundle"])
    assert payload["account"] == "alpha"
    assert payload["bridgeUrl"] == "ws://localhost:19601"
    assert get_pairing_status("alpha")["pairing_pending"] is True

    paired = consume_pairing_session(
        "alpha",
        payload["pairingCode"],
        instance_id="instance_alpha_123",
        extension_id="a" * 32,
        profile_directory="Default",
    )

    assert paired.extension_instance_id == "instance_alpha_123"
    assert paired.bridge_token != old_token
    assert get_pairing_status("alpha")["paired"] is True
    assert not pairing_session_path("alpha").exists()
    with pytest.raises(RuntimeError, match="不存在或已经使用"):
        consume_pairing_session(
            "alpha",
            payload["pairingCode"],
            instance_id="instance_alpha_123",
            extension_id="a" * 32,
            profile_directory="Default",
        )


def test_wrong_and_expired_pairing_codes_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = add_account(
        "alpha",
        bridge_port=19602,
        extension_source=_make_extension(tmp_path / "extension-source"),
    )
    payload = decode_pairing_bundle(create_pairing_session(config)["pairing_bundle"])

    with pytest.raises(RuntimeError, match="不正确"):
        consume_pairing_session(
            "alpha",
            "wrong-code",
            instance_id="instance_alpha_123",
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
            instance_id="instance_alpha_123",
            extension_id="b" * 32,
            profile_directory="Default",
        )


def test_two_accounts_share_code_but_keep_independent_pairings(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    source = _make_extension(tmp_path / "extension-source")
    alpha = add_account("alpha", bridge_port=19603, extension_source=source)
    beta = add_account("beta", bridge_port=19604, extension_source=source)

    assert alpha.extension_dir == beta.extension_dir
    alpha_payload = decode_pairing_bundle(
        create_pairing_session(alpha)["pairing_bundle"]
    )
    beta_payload = decode_pairing_bundle(create_pairing_session(beta)["pairing_bundle"])
    consume_pairing_session(
        "alpha",
        alpha_payload["pairingCode"],
        instance_id="instance_alpha_123",
        extension_id="c" * 32,
        profile_directory="Default",
    )
    consume_pairing_session(
        "beta",
        beta_payload["pairingCode"],
        instance_id="instance_beta_1234",
        extension_id="d" * 32,
        profile_directory="Default",
    )

    assert load_account("alpha").extension_instance_id == "instance_alpha_123"
    assert load_account("beta").extension_instance_id == "instance_beta_1234"
    assert load_account("alpha").bridge_token != load_account("beta").bridge_token


def test_revoke_pairing_clears_instance_and_rotates_token(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = add_account(
        "alpha",
        bridge_port=19605,
        extension_source=_make_extension(tmp_path / "extension-source"),
    )
    payload = decode_pairing_bundle(create_pairing_session(config)["pairing_bundle"])
    paired = consume_pairing_session(
        "alpha",
        payload["pairingCode"],
        instance_id="instance_alpha_123",
        extension_id="e" * 32,
        profile_directory="Default",
    )

    revoked = revoke_account_pairing("alpha")

    assert revoked.extension_instance_id is None
    assert revoked.bridge_token != paired.bridge_token
    assert get_pairing_status("alpha")["paired"] is False


def test_pairing_rejects_a_different_profile_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = add_account(
        "alpha",
        bridge_port=19606,
        extension_source=_make_extension(tmp_path / "extension-source"),
    )
    payload = decode_pairing_bundle(create_pairing_session(config)["pairing_bundle"])

    with pytest.raises(RuntimeError, match="Profile 与账号槽位不一致"):
        consume_pairing_session(
            "alpha",
            payload["pairingCode"],
            instance_id="instance_alpha_123",
            extension_id="f" * 32,
            profile_directory="Profile 2",
        )

    assert pairing_session_path("alpha").exists()
