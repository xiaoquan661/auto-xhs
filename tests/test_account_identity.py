from __future__ import annotations

import argparse
import json

import pytest

from scripts.account_identity import (
    assert_live_identity,
    begin_login_switch,
    cancel_login_switch,
    complete_login_switch,
    identity_status,
    load_identity_history,
    load_switch_state,
    record_current_identity,
)
from scripts.cli import _ensure_switch_allows_command
from scripts.xhs.login import get_current_user_identity


def _identity(user_id: str, nickname: str = "测试账号") -> dict:
    return {
        "logged_in": True,
        "user_id": user_id,
        "nickname": nickname,
        "profile_url": f"https://www.xiaohongshu.com/user/profile/{user_id}",
    }


def test_recorded_identity_matches_and_blocks_drift(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    record_current_identity("alpha", _identity("user-old"), source="test")

    matched = assert_live_identity("alpha", _identity("user-old"))
    assert matched["comparison"] == "match"

    with pytest.raises(RuntimeError, match="登录身份发生变化"):
        assert_live_identity("alpha", _identity("user-other"))


def test_safe_switch_records_history_and_unblocks_new_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    record_current_identity("alpha", _identity("user-old"), source="test")
    pending = begin_login_switch(
        "alpha",
        _identity("user-old"),
        target_user_id="user-new",
        target_label="新主账号",
    )

    assert pending["status"] == "awaiting_login"
    with pytest.raises(RuntimeError, match="正在换号"):
        assert_live_identity("alpha", _identity("user-old"))

    event = complete_login_switch("alpha", _identity("user-new", "新昵称"))

    assert event["from"]["user_id"] == "user-old"
    assert event["to"]["user_id"] == "user-new"
    assert load_switch_state("alpha") is None
    assert identity_status("alpha", _identity("user-new"))["comparison"] == "match"
    assert load_identity_history("alpha")[-1]["event"] == "login-switched"


def test_switch_completion_rejects_wrong_or_same_user(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    begin_login_switch(
        "alpha",
        _identity("user-old"),
        target_user_id="user-new",
    )

    with pytest.raises(RuntimeError, match="与预期不一致"):
        complete_login_switch("alpha", _identity("user-wrong"))
    with pytest.raises(RuntimeError, match="仍是原小红书账号"):
        complete_login_switch("alpha", _identity("user-old"))

    assert load_switch_state("alpha") is not None


def test_cancel_switch_requires_force_after_identity_changed(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    begin_login_switch("alpha", _identity("user-old"))

    with pytest.raises(RuntimeError, match="检测到当前已登录另一个"):
        cancel_login_switch("alpha", _identity("user-new"))

    result = cancel_login_switch("alpha", _identity("user-new"), force=True)
    assert result["cancelled"] is True
    assert load_switch_state("alpha") is None


def test_corrupt_history_lines_are_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    history = tmp_path / "accounts" / "alpha" / "login-identity-history.jsonl"
    history.parent.mkdir(parents=True)
    history.write_text('{"event":"ok"}\nnot-json\n', encoding="utf-8")

    assert load_identity_history("alpha") == [{"event": "ok"}]
    assert json.loads(history.read_text(encoding="utf-8").splitlines()[0]) == {
        "event": "ok"
    }


def test_current_user_identity_extracts_uid_and_nickname():
    class FakePage:
        def __init__(self) -> None:
            self.url = ""

        def navigate(self, url: str) -> None:
            self.url = url

        def wait_for_load(self) -> None:
            pass

        def wait_dom_stable(self) -> None:
            pass

        def has_element(self, selector: str) -> bool:
            return "login-btn" in selector or "channel" in selector

        def evaluate(self, expression: str):
            if expression == "location.href":
                return self.url
            if "getAttribute('href')" in expression:
                return "/user/profile/uid-123?xsec_token=token"
            if "innerText" in expression:
                return "新账号昵称"
            return ""

    identity = get_current_user_identity(FakePage())

    assert identity["logged_in"] is True
    assert identity["user_id"] == "uid-123"
    assert identity["nickname"] == "新账号昵称"


def test_pending_switch_blocks_business_commands_but_allows_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    begin_login_switch("alpha", _identity("user-old"))

    with pytest.raises(RuntimeError, match="业务命令已暂停"):
        _ensure_switch_allows_command(
            argparse.Namespace(account="alpha", allow_during_switch=False)
        )

    _ensure_switch_allows_command(
        argparse.Namespace(account="alpha", allow_during_switch=True)
    )
