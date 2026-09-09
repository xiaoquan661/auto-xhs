"""Background synchronization for manager-side executor caches."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

try:
    from .manager_service import ManagerService
except ImportError:  # Direct execution from the scripts directory.
    from manager_service import ManagerService

HEALTH_ACCOUNT_SYNC_SECONDS = 30.0
RESULT_SYNC_SECONDS = 300.0


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ManagerSyncService:
    """Refresh executor state without coupling SSH/SFTP calls to page requests."""

    def __init__(
        self,
        manager: ManagerService,
        *,
        health_interval_seconds: float = HEALTH_ACCOUNT_SYNC_SECONDS,
        result_interval_seconds: float = RESULT_SYNC_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if health_interval_seconds <= 0 or result_interval_seconds <= 0:
            raise ValueError("经理机同步间隔必须大于0")
        self.manager = manager
        self.health_interval_seconds = float(health_interval_seconds)
        self.result_interval_seconds = float(result_interval_seconds)
        self._monotonic = monotonic
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        with self._lifecycle_lock:
            if self.running:
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run,
                name="manager-background-sync",
                daemon=True,
            )
            self._thread.start()

    def stop(self, *, timeout_seconds: float = 5.0) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
        thread.join(timeout=max(0.0, timeout_seconds))
        with self._lifecycle_lock:
            if self._thread is thread and not thread.is_alive():
                self._thread = None

    def run_health_cycle(self) -> dict:
        return self._run_cycle(
            "health_accounts",
            self.health_interval_seconds,
            self._sync_machine_health_accounts,
        )

    def run_result_cycle(self) -> dict:
        return self._run_cycle(
            "results",
            self.result_interval_seconds,
            self._sync_machine_results,
        )

    def _run(self) -> None:
        next_health = self._monotonic()
        next_results = next_health
        while not self._stop_event.is_set():
            now = self._monotonic()
            if now >= next_health:
                self.run_health_cycle()
                next_health = self._monotonic() + self.health_interval_seconds
            if self._stop_event.is_set():
                break
            now = self._monotonic()
            if now >= next_results:
                self.run_result_cycle()
                next_results = self._monotonic() + self.result_interval_seconds
            delay = max(0.05, min(next_health, next_results) - self._monotonic())
            self._stop_event.wait(delay)

    def _run_cycle(
        self,
        kind: str,
        interval_seconds: float,
        operation: Callable[[str], dict],
    ) -> dict:
        started_at = utc_now()
        outcomes: list[dict] = []
        machines = self.manager.list_machines().get("machines") or []
        for machine in machines:
            machine_id = str(machine.get("machine_id") or "")
            if not machine_id:
                continue
            try:
                outcome = operation(machine_id)
            except Exception as exc:
                outcome = {
                    "success": False,
                    "status": "FAILED",
                    "error_code": type(exc).__name__,
                    "message": str(exc),
                }
            outcomes.append({"machine_id": machine_id, **outcome})

        succeeded = sum(
            item.get("success") is True and item.get("status") != "PARTIAL_SUCCESS"
            for item in outcomes
        )
        failed = len(outcomes) - succeeded
        status = "IDLE" if not outcomes else "SUCCESS" if not failed else "PARTIAL_SUCCESS"
        if outcomes and not any(item.get("success") is True for item in outcomes):
            status = "FAILED"
        completed_at = utc_now()
        next_run_at = (datetime.now(UTC) + timedelta(seconds=interval_seconds)).isoformat()
        summary = {
            "success": failed == 0,
            "status": status,
            "started_at": started_at,
            "completed_at": completed_at,
            "next_run_at": next_run_at,
            "interval_seconds": interval_seconds,
            "machine_total": len(outcomes),
            "succeeded": succeeded,
            "failed": failed,
            "outcomes": outcomes,
        }
        self.manager.record_sync_status(kind, summary)
        return summary

    def _sync_machine_health_accounts(self, machine_id: str) -> dict:
        health = self.manager.check_machine(machine_id)
        machine = health.get("machine") if isinstance(health.get("machine"), dict) else {}
        if machine.get("ssh_state") != "ONLINE":
            return {
                "success": False,
                "status": str(health.get("status") or "UNKNOWN"),
                "health": health,
                "accounts": {"success": False, "status": "SKIPPED"},
            }
        accounts = self.manager.list_machine_accounts(machine_id)
        success = health.get("success") is True and accounts.get("success") is True
        return {
            "success": success,
            "status": "SUCCESS" if success else "PARTIAL_SUCCESS",
            "health": health,
            "accounts": accounts,
        }

    def _sync_machine_results(self, machine_id: str) -> dict:
        result = self.manager.collect_results(machine_id, download_evidence=True)
        return {
            "success": result.get("success") is True,
            "status": str(result.get("status") or "UNKNOWN"),
            "collected": int(result.get("collected") or 0),
            "incremental_collected": int(result.get("incremental_collected") or 0),
            "pending_evidence": int(result.get("pending_evidence") or 0),
            "outbox_collection": result.get("outbox_collection"),
            "error_code": str(result.get("error_code") or ""),
            "message": str(result.get("message") or ""),
        }
