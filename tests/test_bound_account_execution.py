from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from account_identity import begin_login_switch, complete_login_switch, record_current_identity
from application_service import ApplicationService, ServiceError
from product_store import ProductStore

from scripts import cli


@pytest.fixture
def bound_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(tmp_path / "product"))
    record_current_identity(
        "alpha", {"logged_in": True, "user_id": "owner-alpha"}, source="test"
    )
    page = Mock()
    page.navigate.side_effect = AssertionError("业务连接不应跳页")
    observer = Mock(side_effect=AssertionError("业务连接不应读取页面身份"))
    monkeypatch.setattr("xhs.login.get_current_user_identity", observer)
    monkeypatch.setattr(cli, "_connect_runtime", lambda _args: ("alpha", page))
    return page, observer


def test_sequential_business_and_publish_connections_reuse_binding(bound_runtime):
    page, observer = bound_runtime
    for command in ("like-feed", "post-comment", "fill-publish"):
        _, connected = cli._connect(SimpleNamespace(account="alpha", command=command))
        assert connected is page
    for command in ("select-template", "next-step", "click-publish", "save-draft"):
        _, connected = cli._connect_existing(SimpleNamespace(account="alpha", command=command))
        assert connected is page
    observer.assert_not_called()
    page.navigate.assert_not_called()


def test_unbound_account_requires_first_confirmation(bound_runtime):
    with pytest.raises(ServiceError) as exc:
        cli._connect(SimpleNamespace(account="beta", command="post-comment"))
    assert exc.value.code == "IDENTITY_REQUIRED"


def test_switch_completion_changes_execution_identity(bound_runtime):
    begin_login_switch("alpha", {"logged_in": True, "user_id": "owner-alpha"})
    with pytest.raises(ServiceError) as exc:
        cli._connect(SimpleNamespace(account="alpha", command="like-feed"))
    assert exc.value.code == "SWITCH_PENDING"
    complete_login_switch("alpha", {"logged_in": True, "user_id": "owner-new"})
    identity = ApplicationService().require_account_identity("alpha")
    assert identity["execution_user_id"] == "owner-new"
    cli._connect(SimpleNamespace(account="alpha", command="like-feed"))
    bound_runtime[1].assert_not_called()


@pytest.mark.parametrize(
    ("observed", "error"),
    [
        ({"logged_in": False, "user_id": ""}, "LOGIN_REQUIRED"),
        ({"logged_in": True, "user_id": "another-owner"}, "IDENTITY_MISMATCH"),
    ],
)
def test_known_identity_change_blocks_business(bound_runtime, observed, error):
    ProductStore().set_setting("runtime_identity_checks", {"alpha": observed})
    with pytest.raises(ServiceError) as exc:
        cli._connect(SimpleNamespace(account="alpha", command="post-comment"))
    assert exc.value.code == error
    bound_runtime[1].assert_not_called()


def test_explicit_identity_command_reads_page_and_updates_runtime(bound_runtime, monkeypatch):
    observed = {"logged_in": True, "user_id": "owner-alpha"}
    reader = Mock(return_value=observed)
    monkeypatch.setattr("xhs.login.get_current_user_identity", reader)
    _, _, result = cli._observe_login_identity(
        SimpleNamespace(account="alpha", command="account-identity")
    )
    assert result == observed
    reader.assert_called_once_with(bound_runtime[0])
    runtime = ProductStore().get_setting("runtime_identity_checks")["alpha"]
    assert runtime["user_id"] == "owner-alpha"
