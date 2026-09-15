"""Background synchronization for manager-side executor caches."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from manager_sync_log import write_sync_log

try:
    from .manager_service import ManagerService
except ImportError:  # Direct execution from the scripts directory.
    from manager_service import ManagerService


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ManagerSyncService:
    """Refresh executor state without coupling SSH/SFTP calls to page requests."""

    def __init__(
        self,
        manager: ManagerService,
        *,
        health_interval_seconds: float | None = None,
        result_interval_seconds: float | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if any(
            value is not None and value <= 0
            for value in (health_interval_seconds, result_interval_seconds)
        ):
            raise ValueError("经理机同步间隔必须大于0")
        self.manager = manager
        self._health_interval_seconds = health_interval_seconds
        self._result_interval_seconds = result_interval_seconds
        self._monotonic = monotonic
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        store = getattr(manager, "store", None)
        self.log_path = store.root / "logs" / "manager-sync.log" if store else None

    @property
    def health_interval_seconds(self) -> float:
        if self._health_interval_seconds is not None:
            return float(self._health_interval_seconds)
        return float(self.manager.polling_settings["health_accounts_seconds"])

    @property
    def result_interval_seconds(self) -> float:
        if self._result_interval_seconds is not None:
            return float(self._result_interval_seconds)
        return float(self.manager.polling_settings["results_seconds"])

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
        last_health = None
        last_results = None
        while not self._stop_event.is_set():
            now = self._monotonic()
            if last_health is None or now >= last_health + self.health_interval_seconds:
                self.run_health_cycle()
                last_health = self._monotonic()
            if self._stop_event.is_set():
                break
            now = self._monotonic()
            if last_results is None or now >= last_results + self.result_interval_seconds:
                self.run_result_cycle()
                last_results = self._monotonic()
            next_health = last_health + self.health_interval_seconds
            next_results = last_results + self.result_interval_seconds
            delay = min(1.0, max(0.05, min(next_health, next_results) - self._monotonic()))
            self._stop_event.wait(delay)

    def _run_cycle(
        self,
        kind: str,
        interval_seconds: float,
        operation: Callable[[str], dict],
    ) -> dict:
        started_at = utc_now()
        title = "机器状态检查" if kind == "health_accounts" else "运营回执回收"
        write_sync_log(self.log_path, f"{title}：本轮开始")
        outcomes: list[dict] = []
        machines = self.manager.list_machines().get("machines") or []
        for machine in machines:
            machine_id = str(machine.get("machine_id") or "")
            if not machine_id:
                continue
            write_sync_log(self.log_path, f"{machine_id} · {title}：开始")
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
            status = outcome.get("status") or "UNKNOWN"
            description = {"SUCCESS": "完成", "IDLE": "本轮无新增", "FAILED": "失败",
                           "OFFLINE": "离线", "PARTIAL_SUCCESS": "部分完成"}.get(status, status)
            counts = ""
            if kind == "results" and "collected" in outcome:
                counts = (f"，回执 {outcome['collected']} 条，"
                          f"待下载证据 {outcome.get('pending_evidence', 0)} 条")
            write_sync_log(self.log_path, f"{machine_id} · {title}：{description}{counts}")

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
        write_sync_log(self.log_path, f"{title}：本轮结束，成功 {succeeded}/{len(outcomes)} 台，"
                       f"下轮约 {interval_seconds:g}秒后")
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
        machine = next((row for row in self.manager.list_machines()["machines"]
                        if row["machine_id"] == machine_id), {})
        business_data = None
        if machine.get("business_data_sync_enabled"):
            try:
                business_data = self.manager.collect_business_data(machine_id)
            except Exception as exc:
                business_data = {"success": False, "message": str(exc)}
            if not business_data.get("success"):
                result = {**result, "status": "PARTIAL_SUCCESS"}
        return {
            "business_data": business_data,
            "success": result.get("success") is True,
            "status": str(result.get("status") or "UNKNOWN"),
            "collected": int(result.get("collected") or 0),
            "incremental_collected": int(result.get("incremental_collected") or 0),
            "pending_evidence": int(result.get("pending_evidence") or 0),
            "outbox_collection": result.get("outbox_collection"),
            "error_code": str(result.get("error_code") or ""),
            "message": str(result.get("message") or ""),
        }
