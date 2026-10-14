"""Manager incident triage, repair releases, and employee acceptance."""

from __future__ import annotations

import copy
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from debug_common import (
    code_path,
    identifier,
    normalize_incident,
    now,
    repair_relative_path,
    run_tests,
    source_layout,
    write_json,
)
from manager_transport import ManagerTransportError
from project_paths import project_root
from service_errors import ServiceError

if TYPE_CHECKING:
    from manager_service import ManagerService


class ManagerDebugService:
    def __init__(self, manager: ManagerService) -> None:
        self.manager = manager
        self.store = manager.store
        self.root = self.store.root / "debug"

    def ingest_incident(self, machine_id: str, payload: dict) -> dict:
        if payload.get("machine_id") and payload["machine_id"] != machine_id:
            raise ServiceError("INCIDENT_ROUTE_MISMATCH", "故障回执机器不匹配")

        def save(state: dict) -> dict:
            source_key = payload.get("source_key")
            source_id = payload.get("incident_id")
            for item in state["debug_incidents"].values():
                if item["machine_id"] != machine_id:
                    continue
                if (source_key and item.get("source_key") == source_key) or (
                    source_id and item.get("source_incident_id") == source_id
                ):
                    return copy.deepcopy(item)
            record = normalize_incident(payload, machine_id)
            record["source_incident_id"] = source_id or ""
            state["debug_incidents"][record["incident_id"]] = record
            return copy.deepcopy(record)

        return self.store.mutate(save, include_live=False)

    def ingest_receipt(self, machine_id: str, receipt: dict) -> dict:
        release_id = str(receipt.get("release_id") or "")
        release = self.store.get("debug_releases", release_id)
        if not release or release["machine_id"] != machine_id:
            raise ServiceError("REPAIR_ROUTE_MISMATCH", "安装回执与经理发布记录不匹配")
        if (
            receipt.get("machine_id") != machine_id
            or receipt.get("incident_id") != release["incident_id"]
        ):
            raise ServiceError("REPAIR_ROUTE_MISMATCH", "安装回执机器或工单不匹配")
        status = str(receipt.get("status") or "")
        if status not in {"INSTALLING", "INSTALLED", "ROLLED_BACK"}:
            raise ServiceError("INVALID_REPAIR_RECEIPT", "安装回执状态无效")
        if release.get("receipt", {}).get("status") == status and release["state"] not in {
            "RESULT_UNKNOWN",
            "OFFLINE",
        }:
            return release
        # An older installation event may arrive after the direct SSH response.
        ranks = {"INSTALLING": 1, "INSTALLED": 2, "ROLLED_BACK": 3}
        old_status = release.get("receipt", {}).get("status", "")
        if ranks.get(old_status, 0) > ranks[status]:
            return release
        mapped = {
            "INSTALLING": "INSTALLING",
            "INSTALLED": "AWAITING_VERIFICATION",
            "ROLLED_BACK": "ROLLED_BACK",
        }
        release.update(state=mapped[status], receipt=copy.deepcopy(receipt), updated_at=now())
        self.store.put("debug_releases", release_id, release)
        if self._incident(release["incident_id"]).get("release_id") == release_id:
            self._update_incident(release["incident_id"], mapped[status], f"员工安装回执：{status}")
        return release

    def action(self, action: str, payload: dict) -> dict:
        if action == "list":
            incidents = self.store.list("debug_incidents")
            if payload.get("state"):
                incidents = [item for item in incidents if item["state"] == payload["state"]]
            return {
                "success": True,
                "incidents": sorted(incidents, key=lambda x: x["updated_at"], reverse=True),
                "releases": self.store.list("debug_releases"),
            }
        if action == "report":
            machine = self.manager._get_machine(str(payload.get("machine_id") or ""))
            return {
                "success": True,
                "incident": self.ingest_incident(machine["machine_id"], payload),
            }
        if action in {"dispatch", "apply", "reconcile", "verify", "rollback"}:
            return self._release_action(action, payload)
        incident = self._incident(str(payload.get("incident_id") or ""))
        if action == "show":
            return {"success": True, "incident": incident}
        if action == "claim":
            owner = str(payload.get("owner") or "经理 Agent")
            if incident["state"] == "OPEN":
                incident = self._update_incident(
                    incident["incident_id"], "DIAGNOSING", "经理领取诊断", owner=owner
                )
            path = self._handoff(incident)
            return {"success": True, "incident": incident, "handoff_path": str(path)}
        if action == "diagnose":
            self._require_text(payload, "reproduction", "复现过程")
            self._require_text(payload, "root_cause", "根因")
            self._require_text(payload, "resolution", "修复方案")
            incident = self._update_incident(
                incident["incident_id"],
                "FIXING",
                "已记录复现、根因和修复方案",
                diagnosis={
                    name: payload[name] for name in ("reproduction", "root_cause", "resolution")
                },
            )
        elif action == "build":
            return self.build(incident, payload)
        elif action == "accept":
            self._require_text(payload, "acceptance_note", "验收结论")
            release = self._release(str(incident.get("release_id") or ""))
            if release["state"] != "VERIFIED":
                raise ServiceError("REPAIR_NOT_VERIFIED", "员工安装、回归和服务验证通过后才能验收")
            if incident.get("reconciliation_required"):
                self._require_text(payload, "reconciliation_note", "原任务结果核对结论")
            incident = self._update_incident(
                incident["incident_id"],
                "CLOSED",
                "经理验收通过",
                acceptance_note=payload["acceptance_note"],
                reconciliation_note=str(payload.get("reconciliation_note") or ""),
            )
        elif action == "resolve":
            if incident.get("release_id") or incident["category"] == "code":
                raise ServiceError("REPAIR_ACCEPTANCE_REQUIRED", "代码修复请使用验收关闭")
            self._require_text(payload, "resolution", "运行环境处理或结果核对结论")
            incident = self._update_incident(
                incident["incident_id"], "CLOSED", payload["resolution"]
            )
        elif action == "reopen":
            self._require_text(payload, "reason", "重新打开原因")
            incident = self._update_incident(incident["incident_id"], "OPEN", payload["reason"])
        else:
            raise ServiceError("INVALID_DEBUG_ACTION", "未知经理调试操作")
        return {"success": True, "incident": incident}

    def build(self, incident: dict, payload: dict) -> dict:
        if not incident.get("diagnosis"):
            raise ServiceError("REPAIR_DIAGNOSIS_REQUIRED", "先记录复现过程、根因和修复方案")
        project = Path(str(payload.get("project_root") or project_root()))
        if not project.is_absolute():
            raise ServiceError("INVALID_PROJECT_PATH", "项目目录必须使用绝对路径")
        project = project.resolve()
        files = payload.get("files")
        tests = payload.get("tests")
        if not isinstance(files, list) or not files or not isinstance(tests, list) or not tests:
            raise ServiceError("REPAIR_FILES_REQUIRED", "请指定修复文件和回归测试列表")
        files = list(
            dict.fromkeys(
                repair_relative_path(project, relative)
                for relative in [*files, *(test.split("::", 1)[0] for test in tests)]
            )
        )
        content = {relative: code_path(project, relative).read_bytes() for relative in files}
        release_id = identifier("FIX")
        package = self.root / "releases" / release_id
        validation = run_tests(project, tests, self.root / "validation" / release_id)
        if not validation["passed"]:
            return {"success": False, "status": "VALIDATION_FAILED", "validation": validation}
        for relative, data in content.items():
            if code_path(project, relative).read_bytes() != data:
                raise ServiceError(
                    "REPAIR_SOURCE_CHANGED", "测试期间源码发生变化，请重新构建修复包"
                )
        for relative, data in content.items():
            target = code_path(package / "payload", relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        manifest = {
            "schema_version": 1,
            "package_type": "repair_package",
            "package_name": release_id,
            "version": release_id,
            "release_id": release_id,
            "incident_id": incident["incident_id"],
            "target_machine_id": incident["machine_id"],
            "files": files,
            "tests": tests,
            "diagnosis": incident["diagnosis"],
            "validation": validation,
            "created_at": now(),
        }
        layout = source_layout(project)
        if layout:
            manifest["requires_source_layout"] = layout["schema_version"]
            manifest["canonical_files"] = {
                relative: code_path(project, relative).relative_to(project).as_posix()
                for relative in files
            }
        write_json(package / "manifest.json", manifest)
        (package / "INSTALL.md").write_text(
            f"# {incident['summary']}\n\n"
            f"工单：{incident['incident_id']}。目标机器：{incident['machine_id']}。\n\n"
            "通过经理调试流程安装，员工备份源码并运行包内指定测试，失败恢复备份。\n"
            "安装完成后核对服务及原任务结果，由经理验收，再按运营安排恢复机器。\n",
            encoding="utf-8",
        )
        archive = package.with_suffix(".zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for source in package.rglob("*"):
                if source.is_file():
                    bundle.write(source, source.relative_to(package).as_posix())
        record = {
            "release_id": release_id,
            "incident_id": incident["incident_id"],
            "machine_id": incident["machine_id"],
            "state": "READY",
            "created_at": now(),
            "updated_at": now(),
            "package_path": str(package),
            "archive_path": str(archive),
            "manifest": manifest,
        }
        self.store.put("debug_releases", release_id, record)
        incident = self._update_incident(
            incident["incident_id"], "READY", "经理测试通过，修复包已生成", release_id=release_id
        )
        return {"success": True, "incident": incident, "release": record}

    def _release_action(self, action: str, payload: dict) -> dict:
        release = self._release(str(payload.get("release_id") or ""))
        machine = self.manager._get_machine(release["machine_id"])
        release_id = release["release_id"]
        try:
            if action == "dispatch":
                if release["state"] not in {"READY", "OFFLINE", "FAILED", "RESULT_UNKNOWN"}:
                    return {"success": True, "release": release}
                required_layout = release["manifest"].get("requires_source_layout")
                if required_layout is not None:
                    observed = self.manager.transport.invoke(
                        machine, "health", {}, timeout_seconds=30
                    )
                    installed = observed.get("health", {}).get("source_layout", {})
                    if str(installed.get("schema_version")) != str(required_layout):
                        raise ServiceError(
                            "REPAIR_SOURCE_LAYOUT_REQUIRED",
                            "目标机器需要先安装项目职责目录版本，再接收此修复包",
                            409,
                        )
                response = self.manager.transfer(
                    {
                        "transfer_id": release_id,
                        "machine_id": machine["machine_id"],
                        "transfer_type": "repair_package",
                        "source_path": release["package_path"],
                    }
                )
                release["transfer"] = response["transfer"]
                release["state"] = "DELIVERED" if response["success"] else response["status"]
            elif action == "apply":
                if release["state"] not in {"DELIVERED", "DEFERRED"}:
                    raise ServiceError("REPAIR_NOT_DELIVERED", "先下发修复包；结果未知时先回收核对")
                response = self.manager.transport.invoke(
                    machine,
                    "debug-apply",
                    {"release_id": release_id, "transfer_id": release_id},
                    timeout_seconds=360,
                )
                if response.get("receipt"):
                    release = self.ingest_receipt(machine["machine_id"], response["receipt"])
                else:
                    error = response.get("error") or {}
                    release["state"] = (
                        "DEFERRED" if error.get("code") == "REPAIR_DEFERRED" else "FAILED"
                    )
                    release["error"] = error
            elif action == "reconcile":
                response = self.manager.transport.invoke(
                    machine, "debug-status", {"release_id": release_id}
                )
                if response.get("receipt"):
                    release = self.ingest_receipt(machine["machine_id"], response["receipt"])
            elif action == "rollback":
                response = self.manager.transport.invoke(
                    machine, "debug-rollback", {"release_id": release_id}
                )
                if response.get("receipt"):
                    release = self.ingest_receipt(machine["machine_id"], response["receipt"])
                elif not response.get("success"):
                    error = response.get("error") or {}
                    raise ServiceError(
                        str(error.get("code") or "REPAIR_ROLLBACK_FAILED"),
                        str(error.get("message") or "员工回滚失败"),
                    )
            else:
                if release.get("receipt", {}).get("status") != "INSTALLED":
                    raise ServiceError("REPAIR_NOT_INSTALLED", "先取得员工安装成功回执")
                if not release["receipt"].get("validation", {}).get("passed"):
                    raise ServiceError("REPAIR_NOT_VALIDATED", "员工安装后测试尚未通过")
                response = self.manager.transport.invoke(
                    machine, "restart-executor", {}, timeout_seconds=90
                )
                if not response.get("success"):
                    raise ServiceError(
                        "REPAIR_RESTART_FAILED", "员工服务重载失败，请查看执行机诊断"
                    )
                health = self.manager.transport.invoke(machine, "health", {})
                if not health.get("success") or not health.get("health", {}).get(
                    "executor_service", {}
                ).get("ready"):
                    raise ServiceError("REPAIR_HEALTH_FAILED", "员工服务健康回读失败")
                release.update(
                    state="VERIFIED",
                    verification={"restart": response, "health": health, "at": now()},
                )
                self._update_incident(
                    release["incident_id"], "AWAITING_ACCEPTANCE", "员工安装和服务验证通过"
                )
        except ManagerTransportError as exc:
            release.update(
                state="RESULT_UNKNOWN" if exc.result_unknown else "OFFLINE",
                error={"code": exc.code, "message": str(exc)},
            )
        release["updated_at"] = now()
        self.store.put("debug_releases", release_id, release)
        return {
            "success": release["state"]
            not in {"FAILED", "OFFLINE", "RESULT_UNKNOWN", "ROLLED_BACK"},
            "release": release,
        }

    def _incident(self, incident_id: str) -> dict:
        result = self.store.get("debug_incidents", incident_id)
        if not result:
            raise ServiceError("INCIDENT_NOT_FOUND", "故障工单不存在", 404)
        return result

    def _release(self, release_id: str) -> dict:
        result = self.store.get("debug_releases", release_id)
        if not result:
            raise ServiceError("REPAIR_NOT_FOUND", "修复包不存在", 404)
        return result

    def _update_incident(self, incident_id: str, state: str, note: str, **fields: object) -> dict:
        def update(record: dict) -> dict:
            record.update(fields)
            record.update(state=state, updated_at=now())
            record["history"].append({"state": state, "note": note, "at": now()})
            return record

        return self.store.update("debug_incidents", incident_id, update) or self._incident(
            incident_id
        )

    def _handoff(self, incident: dict) -> Path:
        root = self.root / "handoffs" / incident["incident_id"]
        write_json(root / "incident.json", incident)
        path = root / "REPAIR.md"
        path.write_text(
            f"# {incident['summary']}\n\n"
            f"工单：{incident['incident_id']}\n\n"
            f"机器：{incident['machine_id']}；槽位：{incident.get('account_slot', '')}；"
            f"任务：{incident.get('task_id', '')}\n\n"
            "读取同目录 incident.json 和其证据位置，在经理项目分支复现问题。\n"
            "记录根因和修复方案，修改权威源码并补充能复现问题的测试。\n"
            "通过 manager_cli.py debug diagnose 登记诊断，再用 debug build 运行测试并构建修复包。\n"
            "随后 dispatch、apply、reconcile、verify、accept，逐步核对返回状态。\n"
            "原任务结果未知时先读取已有证据并对账。安装与验收保留原任务执行记录。\n",
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _require_text(payload: dict, name: str, label: str) -> None:
        if not str(payload.get(name) or "").strip():
            raise ServiceError("DEBUG_NOTE_REQUIRED", f"请填写{label}")
