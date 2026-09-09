from __future__ import annotations

import copy
from pathlib import Path

import pytest
from employee_operations import PLAN_METRICS
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_transport import ManagerTransportError
from manager_worker import dispatch_request
from manager_worker_service import ManagerWorkerService
from service_errors import ServiceError
from web_server import _dispatch_api, _dispatch_mutation


class Transport:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.response = {"success": True, "archived": True}

    def invoke(self, machine: dict, action: str, payload: dict, **kwargs: object) -> dict:
        self.calls.append((machine["machine_id"], action, payload))
        return self.response


def make_manager(root: Path) -> ManagerService:
    manager = ManagerService(store=ManagerStore(root), transport=Transport())
    manager.store.put("machines", "PC001", {"machine_id": "PC001"})
    manager.store.put(
        "accounts",
        "PC001:alpha",
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "account_key": "PC001:alpha",
        },
    )
    return manager


def plan_request() -> dict:
    return {
        "plan_date": "2026-09-08",
        "accounts": [
            {
                "machine_id": "PC001",
                "account_slot": "alpha",
                "participating": True,
                "metrics": {"browse": 20, "like": 5, "comment": 3, "reply": 0, "publish": 1},
                "notes": "阅读分享",
            }
        ],
    }


def test_plan_roundtrip_does_not_dispatch_and_is_shared_with_api(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    saved = _dispatch_mutation(
        None, "POST", "/api/v1/manager/daily-plans", plan_request(), manager_service=manager
    )
    assert saved["plan"]["version"] == 1
    assert set(saved["plan"]["accounts"][0]["metrics"]) == set(PLAN_METRICS)
    reopened = ManagerService(store=ManagerStore(tmp_path))
    assert (
        _dispatch_api(None, "/api/v1/manager/daily-plans", manager_service=reopened)["plans"][0][
            "accounts"
        ][0]["metrics"]["comment"]
        == 3
    )
    updated = plan_request()
    updated["accounts"][0]["metrics"]["comment"] = 4
    assert manager.save_daily_plan(updated)["plan"]["version"] == 2
    assert len(manager.list_daily_plans()["plans"]) == 1
    assert manager.transport.calls == []
    assert manager.store.list("tasks") == []
    assert manager.store.list("goals") == []


@pytest.mark.parametrize("value", [-1, 1.5, "3"])
def test_plan_quantities_must_be_whole_numbers(tmp_path: Path, value: object) -> None:
    manager = make_manager(tmp_path)
    request = plan_request()
    request["accounts"][0]["metrics"]["like"] = value
    with pytest.raises(ServiceError, match="非负整数"):
        manager.save_daily_plan(request)
    assert manager.list_daily_plans()["plans"] == []


def test_plan_validates_account_and_date(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    request = plan_request()
    request["accounts"].append(copy.deepcopy(request["accounts"][0]))
    with pytest.raises(ServiceError, match="不重复"):
        manager.save_daily_plan(request)
    request = plan_request()
    request["plan_date"] = "2026-02-30"
    with pytest.raises(ServiceError, match="计划日期"):
        manager.save_daily_plan(request)


def test_remote_delete_targets_worker_and_updates_manager_directory(tmp_path: Path) -> None:
    manager = make_manager(tmp_path)
    manager.store.put("account_alerts", "PC001:alpha", {"account_key": "PC001:alpha"})
    request = {
        "action": "delete",
        "account": "alpha",
        "values": {"confirmed": True, "confirmation_name": "alpha"},
    }
    result = _dispatch_mutation(
        None,
        "POST",
        "/api/v1/manager/machines/PC001/account-lifecycle",
        request,
        manager_service=manager,
    )
    assert result["operation"]["archived"]
    assert manager.transport.calls == [("PC001", "account-lifecycle", request)]
    assert manager.store.get("accounts", "PC001:alpha") is None
    assert manager.store.get("account_alerts", "PC001:alpha") is None


def test_uncertain_account_action_is_not_retried(tmp_path: Path, monkeypatch) -> None:
    manager = make_manager(tmp_path)
    calls = []

    def fail(*args: object, **kwargs: object) -> dict:
        calls.append(args)
        raise ManagerTransportError("SSH_TIMEOUT", "连接超时")

    monkeypatch.setattr(manager.transport, "invoke", fail)
    with pytest.raises(ServiceError, match="待核对"):
        manager.manage_remote_account("PC001", {"action": "delete", "account": "alpha"})
    assert len(calls) == 1
    assert manager.store.get("accounts", "PC001:alpha") is not None


@pytest.mark.parametrize(
    "action,method,suffix",
    [
        ("delete", "DELETE", "/accounts/a%20b"),
        ("pair-begin", "POST", "/accounts/a%20b/pairing/begin"),
        ("pair-status", "GET", "/accounts/a%20b/pairing"),
        ("switch-begin", "POST", "/accounts/a%20b/switch/begin"),
        ("switch-complete", "POST", "/accounts/a%20b/switch/complete"),
        ("logout", "POST", "/accounts/a%20b/auth/logout"),
        ("create", "POST", "/accounts"),
        ("import", "POST", "/accounts/import"),
    ],
)
def test_worker_lifecycle_uses_existing_local_account_api(
    tmp_path: Path,
    monkeypatch,
    action: str,
    method: str,
    suffix: str,
) -> None:
    worker = ManagerWorkerService(tmp_path)
    calls = []

    def local_api(verb: str, path: str, body: dict | None = None, **kwargs: object) -> dict:
        calls.append((verb, path, body))
        return {"session_token": "test-session"} if path == "/session" else {"success": True}

    monkeypatch.setattr(worker, "_local_api_request", local_api)
    values = {"confirmed": True, "confirmation_name": "a b"}
    result = dispatch_request(
        worker,
        {
            "action": "account-lifecycle",
            "payload": {
                "action": action,
                "account": "a b",
                "values": values,
            },
        },
    )
    assert result["success"]
    assert calls[-1] == (method, suffix, None if method == "GET" else values)
