"""Employee-local daily notification collection and durable Outbox delivery."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

from account_manager import list_accounts
from employee_operations import flatten_observations
from manager_worker_service import ManagerWorkerService
from metric_temporality import ZONE
from product_store import ProductStore
from project_paths import legacy_script_path
from run_lock import RunLock


def commands(platform: str) -> list[list[str]]:
    return [
        ["collect-operations-metrics", "--source", "notification_history",
         "--notification-kind", "new_followers"],
        ["collect-note-comments", "--max-notes", "100"],
    ]


def eligibility(worker: dict, product: dict, slot: str, config: dict) -> str:
    if worker["machine"].get("paused") or product["settings"].get("global_paused"):
        return "PAUSED"
    management = worker.get("account_management", {}).get(slot, {})
    operations = product["settings"].get(f"operations_config:{slot}") or {}
    if (
        management.get("paused")
        or management.get("scheduling_enabled") is False
        or management.get("status") in {"PAUSED", "DISABLED"}
        or operations.get("enabled") is False
    ):
        return "PAUSED"
    if not config.get("accounts", {}).get(slot, {}).get("enabled", False):
        return "DISABLED"
    active = {"ACCEPTED", "RUNNING", "PAUSE_REQUESTED"}
    if any(
        e.get("task", {}).get("account_slot") == slot and e.get("state") in active
        for e in worker["executions"].values()
    ):
        return "DEFERRED"
    if any(
        t.get("account_slot") == slot and t.get("state") in active
        for t in product.get("tasks", {}).values()
    ):
        return "DEFERRED"
    return "READY"


def observations(value: dict) -> list[dict]:
    result = value.get("result") or {}
    rows = flatten_observations(result.get("inbound_events") or {})
    rows += flatten_observations(result.get("snapshots") or {})
    rows += flatten_observations(result.get("collection_observations") or [])
    return rows


def save(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


class DailyCollector:
    def __init__(self) -> None:
        self.worker = ManagerWorkerService()
        self.product = ProductStore()
        self.root = self.worker.root / "daily-passive"
        self.config = json.loads((self.root / "config.json").read_text(encoding="utf-8-sig"))

    def check(self) -> dict[str, Any]:
        worker, product = self.worker._store.snapshot(), self.product.snapshot()
        return {
            "machine_id": worker["config"]["machine_id"],
            "time": self.config["time"],
            "timezone": "Asia/Shanghai",
            "outbox": worker["config"]["task_outbox_root"],
            "accounts": [
                {
                    "slot": a.name,
                    "platform": a.platform,
                    "status": eligibility(worker, product, a.name, self.config),
                    "channels": [c[0] for c in commands(a.platform)],
                }
                for a in list_accounts()
            ],
        }

    def publish(self, record: dict, path: Path) -> None:
        payload = record["receipt"]
        self.worker._publish_outbox_record(
            "result",
            payload["task_id"],
            payload["remote_execution_id"],
            {"result": payload},
            record_id=f"daily-passive:{payload['task_id']}",
        )
        record["delivered"] = True
        save(path, record)

    def finish(self, record: dict, path: Path, state: str, message: str) -> None:
        now = datetime.now(ZONE).isoformat()
        incremental = [row for channel in record["collections"] for row in observations(channel)]
        record["state"] = state
        record["receipt"] = {
            "task_id": record["task_id"],
            "dispatch_id": record["task_id"],
            "remote_execution_id": record["task_id"],
            "result_revision": 1,
            "machine_id": record["machine_id"],
            "account_slot": record["account_slot"],
            "platform": record["platform"],
            "capability": "collect-operations-metrics",
            "state": state,
            "message": message,
            "completed_at": now,
            "started_at": record["started_at"],
            "incremental_data": incremental,
            "incremental_data_status": "observed",
            "item_results": record["channels"],
            "business_result": {
                "task_type": "daily_passive_collection",
                "statistical_date": record["day"],
                "timezone": "Asia/Shanghai",
                "channels": record["channels"],
            },
            "evidence_index": {"daily_collection": str(path)},
        }
        save(path, record)
        self.publish(record, path)

    def run(self) -> dict[str, Any]:
        lock = RunLock(str(self.root / "schedule.lock"))
        if not lock.acquire(timeout=0.1):
            return {"state": "ALREADY_RUNNING"}
        try:
            return self._run()
        finally:
            lock.release()

    def _run(self) -> dict[str, Any]:
        now = datetime.now(ZONE)
        day = (now.date() - timedelta(days=1)).isoformat()
        # Durable receipts are retried independently of browser collection.
        for path in self.root.glob("*-daily-passive-*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("state") == "RUNNING":
                self.finish(record, path, "FAILED", "上次采集进程中断，请核对已保存的分项结果")
            elif not record.get("delivered"):
                self.publish(record, path)
        outcomes = []
        for account in list_accounts():
            worker, product = self.worker._store.snapshot(), self.product.snapshot()
            machine = worker["config"]["machine_id"]
            task_id = f"{machine.lower()}-{account.name}-daily-passive-{day}"
            path = self.root / f"{task_id}.json"
            if path.exists():
                outcomes.append({"account": account.name, "state": "ALREADY_RECORDED"})
                continue
            status = eligibility(worker, product, account.name, self.config)
            record = {
                "task_id": task_id,
                "machine_id": machine,
                "account_slot": account.name,
                "platform": account.platform,
                "day": day,
                "started_at": now.isoformat(),
                "state": "RUNNING",
                "collections": [],
                "channels": [],
                "delivered": False,
            }
            save(path, record)
            if status != "READY":
                self.finish(record, path, status, "本轮未采集：" + status)
                outcomes.append({"account": account.name, "state": status})
                continue
            account_lock = RunLock(
                str(self.worker.root / "agent-locks" / (quote(account.name, safe="") + ".lock"))
            )
            if not account_lock.acquire(timeout=0.1):
                self.finish(record, path, "DEFERRED", "账号正在执行其他任务")
                outcomes.append({"account": account.name, "state": "DEFERRED"})
                continue
            try:
                for command in commands(account.platform):
                    # Recheck a pause requested while another channel was being collected.
                    if (
                        eligibility(
                            self.worker._store.snapshot(),
                            self.product.snapshot(),
                            account.name,
                            self.config,
                        )
                        != "READY"
                    ):
                        record["channels"].append({"capability": command[0], "state": "PAUSED"})
                        break
                    channel = self.collect(account.name, command)
                    record["collections"].append(channel)
                    record["channels"].append(
                        {
                            "capability": command[0],
                            "state": (channel.get("task") or {}).get("state", "FAILED"),
                            "message": channel.get("error") or "",
                        }
                    )
                    save(path, record)
                states = [c["state"] for c in record["channels"]]
                state = (
                    "SUCCESS"
                    if states and all(s == "SUCCESS" for s in states)
                    else ("PARTIAL_SUCCESS" if incremental_present(record) else "FAILED")
                )
                self.finish(record, path, state, "每日通知采集完成")
                outcomes.append({"account": account.name, "state": state})
            finally:
                account_lock.release()
        return {"statistical_date": day, "accounts": outcomes}

    @staticmethod
    def collect(slot: str, command: list[str]) -> dict:
        try:
            result = subprocess.run(
                [
                    str(Path(sys.executable).with_name("python.exe"))
                    if sys.platform == "win32"
                    else sys.executable,
                    "-X",
                    "utf8",
                    str(legacy_script_path("cli.py")),
                    "--account",
                    slot,
                    *command,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            )
            value = json.loads(result.stdout)
            if result.returncode and not value.get("task"):
                value["task"] = {"state": "FAILED"}
            return value
        except (subprocess.TimeoutExpired, ValueError, OSError) as exc:
            return {"task": {"state": "FAILED"}, "error": str(exc)}


def incremental_present(record: dict) -> bool:
    return any(observations(value) for value in record["collections"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="检查配置，不执行采集")
    args = parser.parse_args()
    collector = DailyCollector()
    result = collector.check() if args.check else collector.run()
    result["checked_at" if args.check else "finished_at"] = datetime.now(ZONE).isoformat()
    save(collector.root / ("last-check.json" if args.check else "last-run.json"), result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
