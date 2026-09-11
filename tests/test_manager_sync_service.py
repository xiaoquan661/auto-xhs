from __future__ import annotations

import threading

from manager_polling import DEFAULT_POLLING
from manager_sync_service import ManagerSyncService


class FakeManager:
    def __init__(self) -> None:
        self.polling_settings = dict(DEFAULT_POLLING)
        self.health_calls: list[str] = []
        self.account_calls: list[str] = []
        self.result_calls: list[str] = []
        self.sync_records: dict[str, dict] = {}
        self.recorded = threading.Event()

    def list_machines(self) -> dict:
        return {
            "success": True,
            "machines": [
                {"machine_id": "PC001"},
                {"machine_id": "PC002"},
            ],
        }

    def check_machine(self, machine_id: str) -> dict:
        self.health_calls.append(machine_id)
        if machine_id == "PC002":
            return {
                "success": False,
                "status": "OFFLINE",
                "machine": {"machine_id": machine_id, "ssh_state": "OFFLINE"},
            }
        return {
            "success": True,
            "status": "READY",
            "machine": {"machine_id": machine_id, "ssh_state": "ONLINE"},
        }

    def list_machine_accounts(self, machine_id: str) -> dict:
        self.account_calls.append(machine_id)
        return {"success": True, "status": "READY", "accounts": []}

    def collect_results(self, machine_id: str, *, download_evidence: bool = True) -> dict:
        assert download_evidence is True
        self.result_calls.append(machine_id)
        if machine_id == "PC002":
            raise RuntimeError("模拟单机回收失败")
        return {
            "success": True,
            "status": "SUCCESS",
            "collected": 1,
            "incremental_collected": 2,
            "pending_evidence": 0,
        }

    def record_sync_status(self, kind: str, values: dict) -> dict:
        self.sync_records[kind] = values
        self.recorded.set()
        return values


class EmptyManager(FakeManager):
    def list_machines(self) -> dict:
        return {"success": True, "machines": []}


def test_health_cycle_isolates_offline_machine_and_skips_second_ssh_read() -> None:
    manager = FakeManager()
    sync = ManagerSyncService(manager)  # type: ignore[arg-type]

    result = sync.run_health_cycle()

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert manager.health_calls == ["PC001", "PC002"]
    assert manager.account_calls == ["PC001"]
    assert manager.sync_records["health_accounts"]["machine_total"] == 2


def test_result_cycle_continues_after_one_machine_raises() -> None:
    manager = FakeManager()
    sync = ManagerSyncService(manager)  # type: ignore[arg-type]

    result = sync.run_result_cycle()

    assert result["status"] == "PARTIAL_SUCCESS"
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert manager.result_calls == ["PC001", "PC002"]
    assert result["outcomes"][1]["error_code"] == "RuntimeError"


def test_background_service_starts_immediately_and_stops() -> None:
    manager = EmptyManager()
    sync = ManagerSyncService(
        manager,  # type: ignore[arg-type]
        health_interval_seconds=0.05,
        result_interval_seconds=0.05,
    )

    sync.start()
    assert manager.recorded.wait(1)
    sync.stop(timeout_seconds=1)

    assert sync.running is False
    assert manager.sync_records["health_accounts"]["status"] == "IDLE"


def test_partial_collection_keeps_pending_counts_and_partial_cycle_status() -> None:
    manager = FakeManager()
    manager.collect_results = lambda machine_id, **kwargs: {
        "success": True, "status": "PARTIAL_SUCCESS", "collected": 1000,
        "outbox_collection": {"pending_records": 50, "imported_records": 1000},
    }
    summary = ManagerSyncService(manager).run_result_cycle()
    assert summary["status"] == "PARTIAL_SUCCESS"
    assert summary["outcomes"][0]["outbox_collection"]["pending_records"] == 50


def test_background_results_repeat_every_five_minutes() -> None:
    now = [0.0]
    result_times: list[float] = []
    manager = EmptyManager()
    sync = ManagerSyncService(manager, monotonic=lambda: now[0])

    class ClockEvent:
        def is_set(self) -> bool:
            return now[0] > 600

        def wait(self, delay: float) -> None:
            now[0] += delay

    def result_cycle() -> dict:
        result_times.append(now[0])
        return {}

    sync._stop_event = ClockEvent()  # type: ignore[assignment]
    sync.run_result_cycle = result_cycle
    sync._run()

    assert result_times == [0.0, 300.0, 600.0]
