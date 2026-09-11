"""Employee incident outbox and manager repair installation."""

from __future__ import annotations

import copy
import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from debug_common import code_path, normalize_incident, now, run_tests, write_json
from run_lock import RunLock
from service_errors import ServiceError

if TYPE_CHECKING:
    from manager_worker_service import ManagerWorkerService


class WorkerDebugService:
    def __init__(self, worker: ManagerWorkerService, project: Path | None = None) -> None:
        self.worker = worker
        self.project = (project or Path(__file__).resolve().parents[1]).resolve()
        self.root = worker.root / "debug"

    def report(self, payload: dict) -> dict:
        def save(state: dict) -> dict:
            reports = state.setdefault("debug_incidents", {})
            values = dict(payload)
            source_key = values.get("source_key")
            execution = values.get("remote_execution_id") or values.get("task_id")
            if not source_key and execution:
                source_key = (
                    f"{state['config']['machine_id']}:{values.get('account_slot', '')}:"
                    f"{execution}:"
                    f"{values.get('error_code') or values.get('source_status') or 'FAILED'}"
                )
                values["source_key"] = source_key
            for record in reports.values():
                if source_key and record.get("source_key") == source_key:
                    return copy.deepcopy(record)
            record = normalize_incident(values, state["config"]["machine_id"])
            reports[record["incident_id"]] = record
            return copy.deepcopy(record)

        record = self.worker._store.mutate(save)
        self.worker._publish_outbox_record(
            "debug_incident",
            record["incident_id"],
            str(record.get("remote_execution_id") or ""),
            {"incident": record},
            record_id=f"incident:{record['incident_id']}",
        )
        return {"success": True, "incident": record}

    def action(self, action: str, payload: dict) -> dict:
        if action == "report":
            return self.report(payload)
        if action == "status":
            records = self.worker._store.snapshot().get("debug_releases", {})
            receipt = records.get(str(payload.get("release_id") or ""))
            return {"success": True, "receipt": receipt}
        if action in {"apply", "rollback"}:
            self.root.mkdir(parents=True, exist_ok=True)
            lock = RunLock(str(self.root / "install.lock"))
            if not lock.acquire(timeout=1):
                raise ServiceError("REPAIR_BUSY", "员工机正在安装或回滚修复包", 409)
            try:
                return self.apply(payload) if action == "apply" else self.rollback(payload)
            finally:
                lock.release()
        raise ServiceError("INVALID_DEBUG_ACTION", "未知员工调试操作")

    def _begin_maintenance(self) -> None:
        def pause(state: dict) -> None:
            busy = [
                item
                for item in state["executions"].values()
                if item.get("state") in {"RUNNING", "PAUSE_REQUESTED"}
            ]
            if busy:
                raise ServiceError("REPAIR_DEFERRED", "员工任务仍在运行，结束后再安装修复包", 409)
            state["machine"]["paused"] = True
            state["machine"]["updated_at"] = now()

        self.worker._store.mutate(pause)
        response = self.worker._sync_local_control("pause-machine")
        if not response.get("success"):
            raise ServiceError("REPAIR_PAUSE_FAILED", "员工本地执行服务暂停失败，请先检查服务状态")

    def _receipt(self, receipt: dict) -> dict:
        def save(state: dict) -> None:
            state.setdefault("debug_releases", {})[receipt["release_id"]] = copy.deepcopy(receipt)

        self.worker._store.mutate(save)
        write_json(self.root / receipt["release_id"] / "receipt.json", receipt)
        self.worker._publish_outbox_record(
            "debug_receipt",
            receipt["release_id"],
            "",
            {"receipt": receipt},
            record_id=f"repair:{receipt['release_id']}:{receipt['status']}",
        )
        return {"success": receipt["status"] == "INSTALLED", "receipt": receipt}

    def apply(self, payload: dict) -> dict:
        state = self.worker._store.snapshot()
        transfer = state["transfers"].get(str(payload.get("transfer_id") or ""))
        if not transfer or transfer.get("state") != "SUCCESS":
            raise ServiceError("REPAIR_NOT_DELIVERED", "修复包尚未传输完成")
        if transfer.get("package_type") != "repair_package":
            raise ServiceError("INVALID_REPAIR_PACKAGE", "请选择修复包传输记录")
        package = Path(transfer["installed_path"])
        manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
        release_id = str(manifest["release_id"])
        if release_id != payload.get("release_id"):
            raise ServiceError("REPAIR_ROUTE_MISMATCH", "修复包编号不匹配")
        if manifest.get("target_machine_id") != state["config"]["machine_id"]:
            raise ServiceError("REPAIR_ROUTE_MISMATCH", "修复包目标机器不匹配")
        existing = state.get("debug_releases", {}).get(release_id)
        if existing:
            if existing["status"] == "INSTALLING":
                raise ServiceError("REPAIR_INTERRUPTED", "安装曾中断，请先回滚并核对")
            return self._receipt(existing)
        if not manifest.get("validation", {}).get("passed"):
            raise ServiceError("REPAIR_NOT_VALIDATED", "经理端修复测试尚未通过")
        files = manifest.get("files") or []
        if not files:
            raise ServiceError("EMPTY_REPAIR_PACKAGE", "修复包缺少文件")
        for relative in files:
            code_path(self.project, relative)
            if not code_path(package / "payload", relative).is_file():
                raise ServiceError("REPAIR_FILE_MISSING", f"修复包缺少文件：{relative}")
        self._begin_maintenance()
        backup_root = self.root / release_id / "backup"
        backup_root.mkdir(parents=True, exist_ok=True)
        plan = []
        for relative in files:
            target = code_path(self.project, relative)
            existed = target.is_file()
            if existed:
                backup = code_path(backup_root, relative)
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, backup)
            plan.append({"path": relative, "existed": existed})
        receipt = {
            "release_id": release_id,
            "incident_id": manifest["incident_id"],
            "machine_id": state["config"]["machine_id"],
            "status": "INSTALLING",
            "backup_root": str(backup_root),
            "files": plan,
            "started_at": now(),
            "project_root": str(self.project),
            "machine_paused": True,
        }
        self._receipt(receipt)
        try:
            for relative in files:
                target = code_path(self.project, relative)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(code_path(package / "payload", relative), target)
                if target.suffix == ".py":
                    compile(target.read_bytes(), str(target), "exec")
            validation = run_tests(
                self.project,
                manifest["tests"],
                self.root / release_id / "validation",
            )
            receipt["validation"] = validation
            if not validation["passed"]:
                raise ServiceError("REPAIR_TEST_FAILED", "员工安装后回归测试失败")
            receipt["status"] = "INSTALLED"
        except Exception as exc:
            receipt["error"] = str(exc)
            self._restore(receipt)
            receipt["status"] = "ROLLED_BACK"
        receipt["completed_at"] = now()
        return self._receipt(receipt)

    def _restore(self, receipt: dict) -> None:
        for item in reversed(receipt["files"]):
            target = code_path(self.project, item["path"])
            if item["existed"]:
                shutil.copy2(code_path(Path(receipt["backup_root"]), item["path"]), target)
            else:
                target.unlink(missing_ok=True)

    def rollback(self, payload: dict) -> dict:
        release_id = str(payload.get("release_id") or "")
        receipt = self.worker._store.snapshot().get("debug_releases", {}).get(release_id)
        if not receipt:
            raise ServiceError("REPAIR_NOT_INSTALLED", "员工机没有此修复安装记录", 404)
        if receipt["status"] == "ROLLED_BACK":
            return self._receipt(receipt)
        self._begin_maintenance()
        self._restore(receipt)
        receipt.update(status="ROLLED_BACK", completed_at=now())
        return self._receipt(receipt)
