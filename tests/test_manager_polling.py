from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from manager_agent_service import ManagerAgentService
from manager_polling import DEFAULT_POLLING
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_sync_service import ManagerSyncService
from service_errors import ServiceError
from web_server import _dispatch_manager_api, _dispatch_manager_mutation


def test_polling_defaults_persistence_api_and_runtime(tmp_path):
    store = ManagerStore(tmp_path)
    manager = ManagerService(store=store)
    sync = ManagerSyncService(manager)
    agent = ManagerAgentService(manager)
    assert manager.polling_settings == DEFAULT_POLLING
    assert sync.health_interval_seconds == 600
    assert sync.result_interval_seconds == 300
    result = _dispatch_manager_mutation(
        manager,
        "POST",
        "/api/v1/manager/polling-settings",
        {"health_accounts_seconds": 900, "results_seconds": 120, "agent_seconds": 10},
    )
    assert result["success"]
    assert sync.health_interval_seconds == 900
    assert sync.result_interval_seconds == 120
    assert agent.interval_seconds == 10
    assert manager.account_stale_seconds == manager.machine_stale_seconds == 2700
    reloaded = ManagerService(store=ManagerStore(tmp_path))
    assert reloaded.polling_settings == manager.polling_settings
    assert (
        _dispatch_manager_api(manager, "/api/v1/manager/polling-settings")["intervals"]
        == result["intervals"]
    )
    assert manager.dashboard_snapshot()["polling"]["intervals"] == result["intervals"]


@pytest.mark.parametrize(
    "values",
    [
        {"health_accounts_seconds": 0},
        {"health_accounts_seconds": True},
        {"results_seconds": 3601},
        {"dashboard_seconds": 1.5},
        {"unknown": 60},
    ],
)
def test_invalid_polling_update_preserves_saved_values(tmp_path, values):
    manager = ManagerService(store=ManagerStore(tmp_path))
    with pytest.raises(ServiceError):
        manager.update_polling_settings(values)
    assert manager.polling_settings == DEFAULT_POLLING


def test_ten_minute_check_keeps_status_fresh_between_cycles(tmp_path):
    manager = ManagerService(store=ManagerStore(tmp_path))
    age = datetime.now(UTC) - timedelta(minutes=9)
    account = {"health_state": "READY", "updated_at": age.isoformat()}
    machine = {"status": "READY", "updated_at": age.isoformat()}
    assert manager._with_account_freshness(account)["health_state"] == "READY"
    assert manager._with_machine_freshness(machine)["status"] == "READY"
    account["updated_at"] = (age - timedelta(minutes=22)).isoformat()
    assert manager._with_account_freshness(account)["health_state"] == "UNKNOWN"


def test_background_health_uses_saved_interval_and_hot_update(tmp_path):
    now = [0.0]
    manager = ManagerService(store=ManagerStore(tmp_path))
    sync = ManagerSyncService(manager, monotonic=lambda: now[0])
    times = []

    class ClockEvent:
        def is_set(self):
            return now[0] > 1500

        def wait(self, delay):
            now[0] += delay
            if now[0] == 650:
                manager.update_polling_settings({"health_accounts_seconds": 900})

    sync._stop_event = ClockEvent()
    sync.run_health_cycle = lambda: times.append(now[0])
    sync.run_result_cycle = lambda: None
    sync._run()
    assert times == [0, 600, 1500]


def test_agent_and_memory_review_follow_updated_intervals(tmp_path):
    now = [0.0]
    manager = ManagerService(store=ManagerStore(tmp_path))
    reviews = []
    manager.memory_action = lambda *args: reviews.append(now[0]) or {}
    manager.update_polling_settings({"memory_review_seconds": 120})
    agent = ManagerAgentService(manager, monotonic=lambda: now[0])
    agent.run_cycle()
    now[0] = 60
    agent.run_cycle()
    assert reviews == [0]
    now[0] = 120
    agent.run_cycle()
    assert reviews == [0, 120]
