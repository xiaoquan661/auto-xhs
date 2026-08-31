"""Manager-side orchestration for Windows executors over a fixed SSH entrypoint."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from manager_store import ManagerStore
from manager_transport import ManagerTransportError, OpenSSHTransport
from service_errors import ServiceError

EXPECTED_EXECUTOR_ROLE = "executor"
REMOTE_TERMINAL_STATES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
    "RESULT_UNKNOWN",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ManagerService:
    """Persist remote intent separately from transport and platform outcomes."""

    def __init__(
        self,
        *,
        store: ManagerStore | None = None,
        transport: OpenSSHTransport | None = None,
    ) -> None:
        self.store = store or ManagerStore()
        self.transport = transport or OpenSSHTransport()

    def register_machine(self, values: dict) -> dict:
        machine = self._normalize_machine(values)
        machine_id = machine["machine_id"]
        existing = self.store.get("machines", machine_id)
        if existing is not None:
            if self._machine_definition(existing) == self._machine_definition(machine):
                return {
                    "success": True,
                    "status": "ALREADY_REGISTERED",
                    "machine": existing,
                }
            raise ServiceError("DUPLICATE_MACHINE_ID", "执行机编号已被其他配置使用", 409)

        endpoint = self._endpoint_key(machine)
        for registered in self.store.list("machines"):
            if self._endpoint_key(registered) == endpoint:
                raise ServiceError(
                    "DUPLICATE_MACHINE_ENDPOINT",
                    "该 SSH 地址、端口和用户已绑定其他执行机编号",
                    409,
                )

        now = utc_now()
        record = {
            **machine,
            "record_id": f"machine:{machine_id}",
            "data_status": "observed",
            "missing_reason": "",
            "machine_role": machine["expected_role"],
            "ssh_state": "UNVERIFIED",
            "installed_version": "",
            "installed_version_status": "missing",
            "status": "REGISTERED",
            "desired_paused": False,
            "remote_paused": None,
            "last_check_at": None,
            "last_seen_at": None,
            "last_health": None,
            "last_error_code": "",
            "last_error": "",
            "created_at": now,
            "updated_at": now,
        }
        return {
            "success": True,
            "status": "REGISTERED",
            "machine": self.store.put("machines", machine_id, record),
        }

    def list_machines(self) -> dict:
        machines = sorted(self.store.list("machines"), key=lambda item: item["machine_id"])
        return {"success": True, "machines": machines}

    def check_machine(self, machine_id: str) -> dict:
        machine = self._get_machine(machine_id)
        machine_id = machine["machine_id"]
        checked_at = utc_now()
        try:
            response = self.transport.invoke(machine, "health", {}, timeout_seconds=20)
        except ManagerTransportError as exc:
            status = "CHECK_UNKNOWN" if exc.result_unknown else "OFFLINE"
            updated = self._update_machine(
                machine_id,
                status=status,
                last_check_at=checked_at,
                ssh_state="CHECK_UNKNOWN" if exc.result_unknown else "OFFLINE",
                last_error_code=exc.code,
                last_error=str(exc),
            )
            return {"success": False, "status": status, "machine": updated}

        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            updated = self._update_machine(
                machine_id,
                status="BLOCKED",
                last_check_at=checked_at,
                ssh_state="ONLINE",
                last_error_code=error_code,
                last_error=message,
            )
            return {"success": False, "status": "BLOCKED", "machine": updated}

        nested_health = response.get("health")
        health = dict(nested_health) if isinstance(nested_health, dict) else dict(response)
        status, error_code, message = self._validate_health(machine, health)
        values = {
            "status": status,
            "last_check_at": checked_at,
            "last_health": health,
            "ssh_state": "ONLINE",
            "installed_version": str(health.get("project_version") or ""),
            "installed_version_status": (
                "observed" if str(health.get("project_version") or "") else "missing"
            ),
            "remote_paused": bool(health.get("paused")),
            "last_error_code": error_code,
            "last_error": message,
        }
        if status in {"READY", "PAUSED"}:
            values["last_seen_at"] = checked_at
        updated = self._update_machine(machine_id, **values)
        return {
            "success": status in {"READY", "PAUSED"},
            "status": status,
            "machine": updated,
            "health": health,
        }

    def dispatch_task(self, request: dict) -> dict:
        normalized = self._normalize_task_request(request)
        task_id = normalized["task_id"]
        machine = self._get_machine(normalized["machine_id"])
        existing = self.store.get("tasks", task_id)
        if existing is not None:
            if existing["request"] != normalized:
                raise ServiceError("DUPLICATE_TASK_ID", "任务编号已被不同请求使用", 409)
            if existing.get("reconciliation_required"):
                return {
                    "success": False,
                    "status": "RECONCILIATION_REQUIRED",
                    "task": existing,
                }
            if existing["dispatch_state"] == "PAUSED":
                return {"success": True, "status": "PAUSED", "task": existing}
            if existing["dispatch_state"] != "PENDING_DISPATCH":
                return {"success": True, "status": "IDEMPOTENT", "task": existing}
            record = existing
        else:
            now = utc_now()
            record = {
                "task_id": task_id,
                "record_id": f"task:{task_id}",
                "data_status": "observed",
                "missing_reason": "",
                "dispatch_id": uuid.uuid4().hex,
                "machine_id": machine["machine_id"],
                "target_machine_id": machine["machine_id"],
                "source_type": normalized["source_type"],
                "function_id": normalized["function_id"],
                "request": normalized,
                "dispatch_state": "PENDING_DISPATCH",
                "business_state": "",
                "remote_execution_id": "",
                "project_version": "",
                "actual_version": "",
                "version_status": "",
                "risk_level": "",
                "last_known_remote_state": "",
                "reconciliation_required": False,
                "connectivity": "UNKNOWN",
                "error_code": "",
                "message": "",
                "recommended_action": "",
                "state": "PENDING_DISPATCH",
                "result_summary": "",
                "created_at": now,
                "updated_at": now,
            }
            record = self.store.put("tasks", task_id, record)

        if machine.get("desired_paused"):
            record = self._update_task(
                task_id,
                dispatch_state="PAUSED",
                error_code="MACHINE_PAUSED",
                message="执行机已暂停，任务保留在经理机",
            )
            return {"success": False, "status": "PAUSED", "task": record}

        if machine.get("status") != "READY":
            check = self.check_machine(machine["machine_id"])
            machine = check["machine"]
            if check["status"] != "READY":
                record = self._update_task(
                    task_id,
                    dispatch_state="PENDING_DISPATCH",
                    connectivity=check["status"],
                    error_code=machine.get("last_error_code") or check["status"],
                    message="执行机当前不可用，任务尚未下发",
                )
                return {
                    "success": False,
                    "status": "PENDING_DISPATCH",
                    "task": record,
                }

        envelope = self._task_envelope(record, machine)
        try:
            accepted = self.transport.invoke(
                machine,
                "accept-task",
                envelope,
                timeout_seconds=30,
            )
        except ManagerTransportError as exc:
            unknown = exc.result_unknown
            record = self._update_task(
                task_id,
                dispatch_state="DISPATCH_UNKNOWN" if unknown else "PENDING_DISPATCH",
                connectivity="CONTACT_LOST" if unknown else "OFFLINE",
                reconciliation_required=unknown,
                error_code=exc.code,
                message=(
                    "下发响应中断，恢复连接后必须先对账"
                    if unknown
                    else "执行机离线，任务尚未确认下发"
                ),
            )
            return {"success": False, "status": record["dispatch_state"], "task": record}

        remote_error = self._remote_error(accepted)
        if remote_error:
            error_code, message = remote_error
            record = self._update_task(
                task_id,
                dispatch_state="BLOCKED",
                connectivity="ONLINE",
                reconciliation_required=False,
                error_code=error_code,
                message=message,
            )
            return {"success": False, "status": "BLOCKED", "task": record}

        execution = self._extract_execution(accepted)
        record = self._update_task_from_execution(task_id, execution, connectivity="ONLINE")
        if execution.get("state") in REMOTE_TERMINAL_STATES:
            return {"success": True, "status": execution["state"], "task": record}

        try:
            started = self.transport.invoke(
                machine,
                "run-task",
                {
                    "task_id": task_id,
                    "remote_execution_id": record.get("remote_execution_id"),
                },
                timeout_seconds=float(normalized.get("timeout_seconds") or 300),
            )
        except ManagerTransportError as exc:
            record = self._update_task(
                task_id,
                dispatch_state="RECONCILIATION_REQUIRED",
                connectivity="CONTACT_LOST",
                reconciliation_required=True,
                error_code=exc.code,
                message="执行状态无法通过 SSH 确认，必须先读取执行机状态",
            )
            return {
                "success": False,
                "status": "RECONCILIATION_REQUIRED",
                "task": record,
            }

        remote_error = self._remote_error(started)
        if remote_error:
            error_code, message = remote_error
            record = self._update_task(
                task_id,
                dispatch_state="BLOCKED",
                business_state="BLOCKED",
                connectivity="ONLINE",
                reconciliation_required=False,
                error_code=error_code,
                message=message,
            )
            return {"success": False, "status": "BLOCKED", "task": record}

        execution = self._extract_execution(started)
        record = self._update_task_from_execution(task_id, execution, connectivity="ONLINE")
        return {
            "success": execution.get("state") not in {"FAILED", "BLOCKED"},
            "status": execution.get("state") or record["dispatch_state"],
            "task": record,
            "execution": execution,
        }

    def get_task(self, task_id: str, *, refresh: bool = False) -> dict:
        task = self._get_task(task_id)
        if not refresh:
            return {"success": True, "task": task}
        return self.reconcile_task(task_id)

    def list_tasks(self) -> dict:
        tasks = sorted(
            self.store.list("tasks"),
            key=lambda item: item["created_at"],
            reverse=True,
        )
        return {"success": True, "tasks": tasks}

    def list_function_versions(
        self,
        machine_id: str,
        function_id: str | None = None,
    ) -> dict:
        machine = self._get_machine(machine_id)
        selected_function = str(function_id or "").strip()
        cached = self._cached_versions(machine["machine_id"], selected_function)
        try:
            response = self.transport.invoke(
                machine,
                "list-function-versions",
                {"function_id": selected_function},
                timeout_seconds=30,
            )
        except ManagerTransportError as exc:
            return {
                "success": False,
                "status": "RESULT_UNKNOWN" if exc.result_unknown else "OFFLINE",
                "machine_id": machine["machine_id"],
                "versions": cached,
                "source": "LAST_KNOWN",
                "error_code": exc.code,
                "message": str(exc),
            }
        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            return {
                "success": False,
                "status": "BLOCKED",
                "machine_id": machine["machine_id"],
                "versions": cached,
                "source": "LAST_KNOWN",
                "error_code": error_code,
                "message": message,
            }
        versions = response.get("versions")
        if not isinstance(versions, list):
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机版本登记返回格式无效")
        cached_versions = [
            self._cache_version(machine["machine_id"], item)
            for item in versions
            if isinstance(item, dict)
        ]
        return {
            "success": True,
            "status": "SUCCESS",
            "machine_id": machine["machine_id"],
            "functions": response.get("functions") or [],
            "versions": cached_versions,
            "source": "REMOTE",
        }

    def set_function_version_policy(self, request: dict) -> dict:
        normalized = self._normalize_version_policy_request(request)
        machine = self._get_machine(normalized["machine_id"])
        try:
            response = self.transport.invoke(
                machine,
                "set-function-version-policy",
                {
                    "function_id": normalized["function_id"],
                    "version": normalized["version"],
                    "status": normalized["status"],
                    "scope": normalized["scope"],
                    "set_as_stable": normalized["set_as_stable"],
                },
                timeout_seconds=30,
            )
        except ManagerTransportError as exc:
            return {
                "success": False,
                "status": "RESULT_UNKNOWN" if exc.result_unknown else "OFFLINE",
                "machine_id": machine["machine_id"],
                "error_code": exc.code,
                "message": str(exc),
            }
        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            return {
                "success": False,
                "status": "BLOCKED",
                "machine_id": machine["machine_id"],
                "error_code": error_code,
                "message": message,
            }
        remote_version = response.get("version")
        if not isinstance(remote_version, dict):
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机版本策略返回格式无效")
        cached = self._cache_version(machine["machine_id"], remote_version)
        return {
            "success": True,
            "status": "IDEMPOTENT" if response.get("idempotent") else "SUCCESS",
            "machine_id": machine["machine_id"],
            "version": cached,
        }

    def list_results(self, machine_id: str | None = None) -> dict:
        results = self.store.list("results")
        incremental_records = self.store.list("incremental_records")
        if machine_id:
            machine_id = self._get_machine(machine_id)["machine_id"]
            results = [item for item in results if item.get("machine_id") == machine_id]
            incremental_records = [
                item for item in incremental_records if item.get("machine_id") == machine_id
            ]
        results.sort(
            key=lambda item: (int(item.get("sequence") or 0), item.get("collected_at") or ""),
            reverse=True,
        )
        return {
            "success": True,
            "results": results,
            "incremental_records": incremental_records,
            "summary": self._summarize_results(results),
            "incremental_unique_count": len(incremental_records),
        }

    def pause_task(self, task_id: str) -> dict:
        task = self._get_task(task_id)
        if task["dispatch_state"] == "PAUSED":
            return {"success": True, "status": "PAUSED", "task": task}
        if task["dispatch_state"] == "PENDING_DISPATCH":
            task = self._update_task(
                task_id,
                dispatch_state="PAUSED",
                message="任务尚未下发，已在经理机暂停",
            )
            return {"success": True, "status": "PAUSED", "task": task}
        if task["business_state"] in REMOTE_TERMINAL_STATES:
            return {"success": True, "status": task["business_state"], "task": task}
        return self._remote_task_action(task, "pause-task")

    def resume_task(self, task_id: str) -> dict:
        task = self._get_task(task_id)
        if task.get("reconciliation_required"):
            reconciled = self.reconcile_task(task_id)
            task = reconciled["task"]
            if task.get("reconciliation_required"):
                return reconciled
        if task["dispatch_state"] == "PAUSED" and not task.get("remote_execution_id"):
            task = self._update_task(
                task_id,
                dispatch_state="PENDING_DISPATCH",
                message="任务已恢复，等待再次下发",
            )
            return {"success": True, "status": "PENDING_DISPATCH", "task": task}
        if task["business_state"] in REMOTE_TERMINAL_STATES:
            return {"success": True, "status": task["business_state"], "task": task}
        result = self._remote_task_action(task, "resume-task")
        resumed = result["task"]
        if resumed.get("last_known_remote_state") == "ACCEPTED":
            machine = self._get_machine(resumed["machine_id"])
            try:
                response = self.transport.invoke(
                    machine,
                    "run-task",
                    {
                        "task_id": task_id,
                        "remote_execution_id": resumed.get("remote_execution_id"),
                    },
                    timeout_seconds=float(task["request"].get("timeout_seconds") or 300),
                )
            except ManagerTransportError as exc:
                resumed = self._update_task(
                    task_id,
                    dispatch_state="RECONCILIATION_REQUIRED",
                    connectivity="CONTACT_LOST",
                    reconciliation_required=True,
                    error_code=exc.code,
                    message="恢复后的执行状态未知，必须先对账",
                )
                return {
                    "success": False,
                    "status": "RECONCILIATION_REQUIRED",
                    "task": resumed,
                }
            remote_error = self._remote_error(response)
            if remote_error:
                error_code, message = remote_error
                resumed = self._update_task(
                    task_id,
                    dispatch_state="BLOCKED",
                    business_state="BLOCKED",
                    connectivity="ONLINE",
                    reconciliation_required=False,
                    error_code=error_code,
                    message=message,
                )
                return {"success": False, "status": "BLOCKED", "task": resumed}
            execution = self._extract_execution(response)
            resumed = self._update_task_from_execution(task_id, execution, connectivity="ONLINE")
            return {"success": True, "status": execution.get("state"), "task": resumed}
        return result

    def pause_machine(self, machine_id: str) -> dict:
        return self._set_machine_pause(machine_id, paused=True)

    def resume_machine(self, machine_id: str) -> dict:
        return self._set_machine_pause(machine_id, paused=False)

    def transfer(self, request: dict) -> dict:
        normalized = self._normalize_transfer_request(request)
        transfer_id = normalized["transfer_id"]
        machine = self._get_machine(normalized["machine_id"])
        existing = self.store.get("transfers", transfer_id)
        if existing is not None:
            if existing["request"] != normalized:
                raise ServiceError("DUPLICATE_TRANSFER_ID", "传输编号已被不同请求使用", 409)
            if existing["state"] == "SUCCESS":
                return {"success": True, "status": "IDEMPOTENT", "transfer": existing}
            record = existing
        else:
            now = utc_now()
            record = {
                "transfer_id": transfer_id,
                "record_id": f"transfer:{transfer_id}",
                "data_status": "observed",
                "missing_reason": "",
                "machine_id": machine["machine_id"],
                "transfer_type": normalized["transfer_type"],
                "request": normalized,
                "state": "PREPARING",
                "remote_staging_path": "",
                "remote_path": "",
                "actual_version": "",
                "installed_path": "",
                "function_id": "",
                "version_status": "",
                "stable_version": "",
                "enabled_scope": {},
                "business_module": "",
                "error_code": "",
                "message": "",
                "created_at": now,
                "updated_at": now,
            }
            record = self.store.put("transfers", transfer_id, record)

        try:
            prepared = self.transport.invoke(
                machine,
                "prepare-transfer",
                {
                    "transfer_id": transfer_id,
                    "transfer_type": normalized["transfer_type"],
                    "source_name": Path(normalized["source_path"]).name,
                },
                timeout_seconds=30,
            )
            remote_error = self._remote_error(prepared)
            if remote_error:
                error_code, message = remote_error
                record = self._update_transfer(
                    transfer_id,
                    state="FAILED",
                    error_code=error_code,
                    message=message,
                )
                return {"success": False, "status": "FAILED", "transfer": record}
            remote = dict(prepared.get("transfer") or prepared)
            remote_state = str(remote.get("state") or "").upper()
            if remote_state == "SUCCESS":
                projection = self._remote_transfer_projection(remote)
                record = self._update_transfer(
                    transfer_id,
                    state="SUCCESS",
                    **projection,
                    error_code="",
                    message=str(remote.get("message") or "执行机已确认该传输完成"),
                )
                self._cache_transfer_version(machine["machine_id"], projection)
                return {"success": True, "status": "IDEMPOTENT", "transfer": record}
            if remote_state == "FAILED":
                record = self._update_transfer(
                    transfer_id,
                    state="FAILED",
                    error_code=str(remote.get("error_code") or "REMOTE_TRANSFER_FAILED"),
                    message=str(remote.get("message") or "执行机已确认该传输失败"),
                )
                return {"success": False, "status": "FAILED", "transfer": record}
            staging_path = str(remote.get("staging_path") or "")
            if not staging_path:
                raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机未返回传输暂存目录")
            record = self._update_transfer(
                transfer_id,
                state="UPLOADING",
                remote_staging_path=staging_path,
                error_code="",
                message="",
            )
            remote_source = self._join_remote_path(
                staging_path,
                Path(normalized["source_path"]).name,
            )
            self.transport.upload(
                machine,
                normalized["source_path"],
                remote_source,
                timeout_seconds=float(normalized.get("timeout_seconds") or 600),
            )
            finalized = self.transport.invoke(
                machine,
                "finalize-transfer",
                {
                    "transfer_id": transfer_id,
                    "transfer_type": normalized["transfer_type"],
                    "uploaded_path": remote_source,
                    "allowed_versions": normalized["allowed_versions"],
                },
                timeout_seconds=120,
            )
        except ManagerTransportError as exc:
            record = self._update_transfer(
                transfer_id,
                state="RESULT_UNKNOWN" if exc.result_unknown else "FAILED",
                error_code=exc.code,
                message=str(exc),
            )
            return {"success": False, "status": record["state"], "transfer": record}
        except ServiceError:
            raise
        except Exception as exc:
            record = self._update_transfer(
                transfer_id,
                state="FAILED",
                error_code="TRANSFER_FAILED",
                message=str(exc),
            )
            return {"success": False, "status": "FAILED", "transfer": record}

        remote_error = self._remote_error(finalized)
        if remote_error:
            error_code, message = remote_error
            record = self._update_transfer(
                transfer_id,
                state="FAILED",
                error_code=error_code,
                message=message,
            )
            return {"success": False, "status": "FAILED", "transfer": record}

        remote = dict(finalized.get("transfer") or finalized)
        remote_state = str(remote.get("state") or "FAILED")
        projection = self._remote_transfer_projection(remote)
        record = self._update_transfer(
            transfer_id,
            state=remote_state,
            **projection,
            error_code=str(remote.get("error_code") or ""),
            message=str(remote.get("message") or ""),
        )
        if remote_state == "SUCCESS":
            self._cache_transfer_version(machine["machine_id"], projection)
        return {"success": remote_state == "SUCCESS", "status": remote_state, "transfer": record}

    def collect_results(self, machine_id: str, *, download_evidence: bool = True) -> dict:
        machine = self._get_machine(machine_id)
        machine_id = machine["machine_id"]
        cursor = self.store.get_collection_cursor(machine_id)
        evidence_errors = self._retry_pending_evidence(machine) if download_evidence else []
        try:
            response = self.transport.invoke(
                machine,
                "collect-results",
                {"since_sequence": cursor},
                timeout_seconds=60,
            )
        except ManagerTransportError as exc:
            return {
                "success": False,
                "status": "OFFLINE" if not exc.result_unknown else "RESULT_UNKNOWN",
                "machine_id": machine_id,
                "cursor": cursor,
                "collected": 0,
                "pending_evidence": self._pending_evidence_count(machine_id),
                "evidence_errors": evidence_errors,
                "error_code": exc.code,
                "message": str(exc),
            }

        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            return {
                "success": False,
                "status": "BLOCKED",
                "machine_id": machine_id,
                "cursor": cursor,
                "collected": 0,
                "pending_evidence": self._pending_evidence_count(machine_id),
                "evidence_errors": evidence_errors,
                "error_code": error_code,
                "message": message,
            }

        results = list(response.get("results") or [])
        inserted = 0
        duplicates = 0
        incremental_inserted = 0
        incremental_duplicates = 0
        for item in results:
            result = {**item, "machine_id": machine_id, "collected_at": utc_now()}
            sequence = int(result.get("sequence") or 0)
            remote_execution_id = str(result.get("remote_execution_id") or "")
            revision = str(result.get("result_revision") or sequence)
            result_key = f"{machine_id}:{remote_execution_id}:{revision}"
            result.update(
                {
                    "record_id": f"result:{result_key}",
                    "created_at": str(result.get("recorded_at") or result["collected_at"]),
                    "data_status": "observed",
                    "missing_reason": "",
                }
            )
            _, created = self.store.record_result(result_key, result)
            inserted += int(created)
            duplicates += int(not created)
            new_records, replayed_records = self._record_incremental_data(machine_id, result)
            incremental_inserted += new_records
            incremental_duplicates += replayed_records
            self._apply_collected_result(result)
            if download_evidence:
                evidence_errors.extend(self._download_evidence(machine, result))

        next_sequence = max(
            cursor,
            int(response.get("next_sequence") or 0),
            *(int(item.get("sequence") or 0) for item in results),
        )
        self.store.set_collection_cursor(machine_id, next_sequence)
        summary = self._summarize_results(results)
        status = "PARTIAL_SUCCESS" if evidence_errors else "SUCCESS"
        return {
            "success": True,
            "status": status,
            "machine_id": machine_id,
            "previous_cursor": cursor,
            "next_cursor": next_sequence,
            "collected": inserted,
            "duplicates": duplicates,
            "incremental_collected": incremental_inserted,
            "incremental_duplicates": incremental_duplicates,
            "state_counts": summary["task_state_counts"],
            "summary": summary,
            "evidence_errors": evidence_errors,
            "pending_evidence": self._pending_evidence_count(machine_id),
            "results": results,
        }

    def _record_incremental_data(self, machine_id: str, result: dict) -> tuple[int, int]:
        if str(result.get("incremental_data_status") or "observed") != "observed":
            return 0, 0
        values = result.get("incremental_data")
        if not isinstance(values, list):
            return 0, 0
        inserted = 0
        duplicates = 0
        for index, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            record_type = str(
                value.get("record_type")
                or value.get("kind")
                or value.get("entity_type")
                or "record"
            )
            identifier = str(
                value.get("natural_key")
                or value.get("record_id")
                or value.get("event_id")
                or value.get("snapshot_id")
                or value.get("source_content_id")
                or value.get("id")
                or ""
            )
            if not identifier:
                identifier = json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            record_key = f"{machine_id}:{record_type}:{identifier}"
            record = {
                "created_at": utc_now(),
                "data_status": "observed",
                "missing_reason": "",
                "machine_id": machine_id,
                "record_type": record_type,
                "record_id": identifier,
                "data": value,
                "source_task_id": str(result.get("task_id") or ""),
                "source_execution_id": str(result.get("remote_execution_id") or ""),
                "source_revision": str(
                    result.get("result_revision") or result.get("sequence") or index
                ),
                "collected_at": utc_now(),
            }
            _, created = self.store.record_incremental(record_key, record)
            inserted += int(created)
            duplicates += int(not created)
        return inserted, duplicates

    def reconcile_task(self, task_id: str) -> dict:
        task = self._get_task(task_id)
        machine = self._get_machine(task["machine_id"])
        try:
            response = self.transport.invoke(
                machine,
                "task-status",
                {
                    "task_id": task_id,
                    "remote_execution_id": task.get("remote_execution_id"),
                },
                timeout_seconds=30,
            )
        except ManagerTransportError as exc:
            task = self._update_task(
                task_id,
                connectivity="OFFLINE" if not exc.result_unknown else "CONTACT_LOST",
                reconciliation_required=True,
                error_code=exc.code,
                message="执行机暂时无法对账，保留最后已知状态",
            )
            return {
                "success": False,
                "status": "RECONCILIATION_REQUIRED",
                "task": task,
            }

        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            if error_code in {"TASK_NOT_FOUND", "EXECUTION_NOT_FOUND"}:
                task = self._update_task(
                    task_id,
                    dispatch_state="PENDING_DISPATCH",
                    connectivity="ONLINE",
                    reconciliation_required=False,
                    error_code="",
                    message="执行机确认未接收该任务，可安全重新下发",
                )
                return {"success": True, "status": "PENDING_DISPATCH", "task": task}
            task = self._update_task(
                task_id,
                connectivity="ONLINE",
                reconciliation_required=True,
                error_code=error_code,
                message=message,
            )
            return {
                "success": False,
                "status": "RECONCILIATION_REQUIRED",
                "task": task,
            }
        execution = self._extract_execution(response)
        task = self._update_task_from_execution(task_id, execution, connectivity="ONLINE")
        return {
            "success": True,
            "status": execution.get("state") or task["dispatch_state"],
            "task": task,
            "execution": execution,
        }

    def _remote_task_action(self, task: dict, action: str) -> dict:
        machine = self._get_machine(task["machine_id"])
        try:
            response = self.transport.invoke(
                machine,
                action,
                {
                    "task_id": task["task_id"],
                    "remote_execution_id": task.get("remote_execution_id"),
                },
                timeout_seconds=30,
            )
        except ManagerTransportError as exc:
            updated = self._update_task(
                task["task_id"],
                dispatch_state="RECONCILIATION_REQUIRED",
                connectivity="CONTACT_LOST",
                reconciliation_required=True,
                error_code=exc.code,
                message="远程状态变更未确认，恢复连接后必须先对账",
            )
            return {
                "success": False,
                "status": "RECONCILIATION_REQUIRED",
                "task": updated,
            }
        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            updated = self._update_task(
                task["task_id"],
                connectivity="ONLINE",
                error_code=error_code,
                message=message,
            )
            return {"success": False, "status": "BLOCKED", "task": updated}
        execution = self._extract_execution(response)
        updated = self._update_task_from_execution(
            task["task_id"], execution, connectivity="ONLINE"
        )
        return {
            "success": response.get("success", True),
            "status": execution.get("state") or updated["dispatch_state"],
            "task": updated,
            "execution": execution,
        }

    def _set_machine_pause(self, machine_id: str, *, paused: bool) -> dict:
        machine = self._get_machine(machine_id)
        machine_id = machine["machine_id"]
        machine = self._update_machine(
            machine_id,
            desired_paused=paused,
            updated_at=utc_now(),
        )
        action = "pause-machine" if paused else "resume-machine"
        try:
            response = self.transport.invoke(machine, action, {}, timeout_seconds=30)
        except ManagerTransportError as exc:
            machine = self._update_machine(
                machine_id,
                status="OFFLINE",
                last_error_code=exc.code,
                last_error=str(exc),
            )
            return {
                "success": False,
                "status": "PAUSE_PENDING" if paused else "RESUME_PENDING",
                "machine": machine,
            }
        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            machine = self._update_machine(
                machine_id,
                status="BLOCKED",
                last_error_code=error_code,
                last_error=message,
            )
            return {
                "success": False,
                "status": "PAUSE_PENDING" if paused else "RESUME_PENDING",
                "machine": machine,
            }
        remote = dict(response.get("machine") or response)
        machine = self._update_machine(
            machine_id,
            remote_paused=bool(remote.get("paused", paused)),
            status="PAUSED" if paused else "READY",
            last_seen_at=utc_now(),
            last_error_code="",
            last_error="",
        )
        return {
            "success": True,
            "status": "PAUSED" if paused else "READY",
            "machine": machine,
        }

    def _apply_collected_result(self, result: dict) -> None:
        state = str(result.get("state") or "")
        if state not in REMOTE_TERMINAL_STATES:
            return
        task_id = str(result.get("task_id") or "")
        task = self.store.get("tasks", task_id) if task_id else None
        if task is None:
            remote_execution_id = str(result.get("remote_execution_id") or "")
            task = next(
                (
                    item
                    for item in self.store.list("tasks")
                    if item.get("remote_execution_id") == remote_execution_id
                ),
                None,
            )
        if task is None:
            return
        self._update_task(
            task["task_id"],
            dispatch_state="COMPLETED",
            business_state=state,
            last_known_remote_state=state,
            actual_version=str(
                result.get("actual_version")
                or result.get("project_version")
                or task.get("actual_version")
                or ""
            ),
            project_version=str(result.get("project_version") or task.get("project_version") or ""),
            function_id=str(result.get("function_id") or task.get("function_id") or ""),
            version_status=str(result.get("version_status") or task.get("version_status") or ""),
            reconciliation_required=state == "RESULT_UNKNOWN",
            connectivity="ONLINE",
            error_code=str(result.get("error_code") or ""),
            message=str(result.get("result_summary") or result.get("message") or ""),
            risk_level=str(result.get("risk_level") or task.get("risk_level") or ""),
            recommended_action=str(result.get("recommended_action") or ""),
        )

    def _download_evidence(self, machine: dict, result: dict) -> list[dict]:
        errors: list[dict] = []
        machine_component = self._safe_path_component(machine["machine_id"])
        execution_component = self._safe_path_component(
            str(result.get("remote_execution_id") or "unknown")
        )
        revision_component = self._safe_path_component(
            str(result.get("result_revision") or result.get("sequence") or "initial")
        )
        target_root = (
            self.store.root
            / "collected"
            / machine_component
            / execution_component
            / revision_component
        )
        for index, item in enumerate(self._evidence_items(result.get("evidence_index"))):
            relative = str(item.get("relative_path") or Path(item["remote_path"]).name)
            parts = self._safe_evidence_parts(relative, fallback=f"evidence-{index + 1}")
            target = target_root.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            evidence_key = (
                f"{machine['machine_id']}:{execution_component}:{revision_component}:{index}"
            )
            record = {
                "evidence_key": evidence_key,
                "machine_id": machine["machine_id"],
                "remote_execution_id": str(result.get("remote_execution_id") or ""),
                "result_revision": str(
                    result.get("result_revision") or result.get("sequence") or "initial"
                ),
                "index": index,
                "remote_path": str(item["remote_path"]),
                "destination": str(target),
                "state": "PENDING",
                "error_code": "",
                "message": "",
                "updated_at": utc_now(),
            }
            existing = self.store.get("evidence_downloads", evidence_key)
            if existing and existing.get("state") == "SUCCESS":
                continue
            error = self._attempt_evidence_download(machine, record)
            if error:
                errors.append(error)
        return errors

    def _retry_pending_evidence(self, machine: dict) -> list[dict]:
        errors: list[dict] = []
        pending = [
            item
            for item in self.store.list("evidence_downloads")
            if item.get("machine_id") == machine["machine_id"] and item.get("state") != "SUCCESS"
        ]
        for record in pending:
            error = self._attempt_evidence_download(machine, record)
            if error:
                errors.append(error)
        return errors

    def _attempt_evidence_download(self, machine: dict, record: dict) -> dict | None:
        target = Path(str(record["destination"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_file():
            self.store.put(
                "evidence_downloads",
                record["evidence_key"],
                {**record, "state": "SUCCESS", "updated_at": utc_now()},
            )
            return None
        try:
            self.transport.download(
                machine,
                str(record["remote_path"]),
                str(target),
                timeout_seconds=120,
            )
        except ManagerTransportError as exc:
            pending = {
                **record,
                "state": "PENDING",
                "error_code": exc.code,
                "message": str(exc),
                "updated_at": utc_now(),
            }
            self.store.put("evidence_downloads", record["evidence_key"], pending)
            return {
                "index": int(record.get("index") or 0),
                "remote_path": str(record["remote_path"]),
                "error_code": exc.code,
                "message": str(exc),
            }
        self.store.put(
            "evidence_downloads",
            record["evidence_key"],
            {
                **record,
                "state": "SUCCESS",
                "error_code": "",
                "message": "",
                "updated_at": utc_now(),
            },
        )
        return None

    def _pending_evidence_count(self, machine_id: str) -> int:
        return sum(
            1
            for item in self.store.list("evidence_downloads")
            if item.get("machine_id") == machine_id and item.get("state") != "SUCCESS"
        )

    @staticmethod
    def _evidence_items(value: object) -> list[dict]:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict) and item.get("remote_path")]
        if not isinstance(value, dict):
            return []
        items: list[dict] = []
        for relative_path, raw in value.items():
            values = raw if isinstance(raw, list) else [raw]
            for item in values:
                if isinstance(item, str):
                    items.append({"remote_path": item, "relative_path": str(relative_path)})
                elif isinstance(item, dict) and item.get("remote_path"):
                    items.append({"relative_path": str(relative_path), **item})
        return items

    @staticmethod
    def _safe_path_component(value: str) -> str:
        cleaned = "".join(
            character if character.isalnum() or character in {"-", "_", "."} else "_"
            for character in value
        ).strip(".")
        return cleaned or "unknown"

    @classmethod
    def _safe_evidence_parts(cls, value: str, *, fallback: str) -> list[str]:
        parts = [
            cls._safe_path_component(part)
            for part in value.replace("\\", "/").split("/")
            if part not in {"", ".", ".."}
        ]
        return parts or [fallback]

    @staticmethod
    def _summarize_results(results: list[dict]) -> dict:
        task_states: Counter[str] = Counter()
        item_states: Counter[str] = Counter()
        error_classes: Counter[str] = Counter()
        item_data_statuses: Counter[str] = Counter()
        incremental_data_statuses: Counter[str] = Counter()
        manual_actions: list[dict] = []
        data_gaps: list[dict] = []
        incremental_records = 0

        for result in results:
            task_states[str(result.get("state") or "UNKNOWN")] += 1
            error_class = str(result.get("error_classification") or result.get("error_code") or "")
            if error_class:
                error_classes[error_class] += 1

            item_status = str(
                result.get("item_results_status")
                or ("observed" if "item_results" in result else "missing")
            )
            item_data_statuses[item_status] += 1
            item_results = result.get("item_results")
            if item_status == "observed" and isinstance(item_results, list):
                for item in item_results:
                    if not isinstance(item, dict):
                        continue
                    item_states[str(item.get("state") or "UNKNOWN")] += 1
                    item_error = str(
                        item.get("error_classification") or item.get("error_code") or ""
                    )
                    if item_error:
                        error_classes[item_error] += 1
            elif item_status != "observed":
                data_gaps.append(
                    {
                        "task_id": str(result.get("task_id") or ""),
                        "data_type": "item_results",
                        "data_status": item_status,
                        "missing_reason": str(
                            result.get("item_results_missing_reason") or "未提供逐项结果"
                        ),
                    }
                )

            incremental_status = str(
                result.get("incremental_data_status")
                or ("observed" if "incremental_data" in result else "missing")
            )
            incremental_data_statuses[incremental_status] += 1
            incremental_data = result.get("incremental_data")
            if incremental_status == "observed" and isinstance(incremental_data, list):
                incremental_records += len(incremental_data)
            elif incremental_status != "observed":
                data_gaps.append(
                    {
                        "task_id": str(result.get("task_id") or ""),
                        "data_type": "incremental_data",
                        "data_status": incremental_status,
                        "missing_reason": str(
                            result.get("incremental_data_missing_reason") or "未提供增量数据"
                        ),
                    }
                )

            if result.get("manual_action_required"):
                manual_actions.append(
                    {
                        "task_id": str(result.get("task_id") or ""),
                        "remote_execution_id": str(result.get("remote_execution_id") or ""),
                        "state": str(result.get("state") or "UNKNOWN"),
                        "recommended_action": str(result.get("recommended_action") or ""),
                    }
                )

        item_status = ManagerService._combined_data_status(item_data_statuses)
        incremental_status = ManagerService._combined_data_status(incremental_data_statuses)
        return {
            "task_state_counts": dict(sorted(task_states.items())),
            "item_results_status": item_status,
            "item_results_data_status_counts": dict(sorted(item_data_statuses.items())),
            "item_state_counts": dict(sorted(item_states.items())),
            "error_class_counts": dict(sorted(error_classes.items())),
            "manual_action_count": len(manual_actions),
            "manual_actions": manual_actions,
            "incremental_data_status": incremental_status,
            "incremental_data_status_counts": dict(sorted(incremental_data_statuses.items())),
            "incremental_record_count": incremental_records,
            "data_gaps": data_gaps,
        }

    @staticmethod
    def _combined_data_status(statuses: Counter[str]) -> str:
        if not statuses:
            return "missing"
        if len(statuses) == 1:
            return next(iter(statuses))
        return "mixed"

    @staticmethod
    def _remote_error(response: dict) -> tuple[str, str] | None:
        if response.get("success") is not False:
            return None
        error = response.get("error") if isinstance(response.get("error"), dict) else {}
        details = next(
            (
                response.get(name)
                for name in ("execution", "task", "transfer", "machine")
                if isinstance(response.get(name), dict)
            ),
            {},
        )
        code = str(
            response.get("error_code")
            or error.get("code")
            or details.get("error_code")
            or "REMOTE_REJECTED"
        )
        message = str(
            response.get("message")
            or error.get("message")
            or details.get("message")
            or "执行机拒绝了本次请求"
        )
        return code, message

    @staticmethod
    def _extract_execution(response: dict) -> dict:
        execution = response.get("execution") or response.get("task") or response
        if not isinstance(execution, dict):
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机返回的任务状态无效")
        return dict(execution)

    def _update_task_from_execution(
        self,
        task_id: str,
        execution: dict,
        *,
        connectivity: str,
    ) -> dict:
        current = self._get_task(task_id)
        state = str(execution.get("state") or "ACCEPTED")
        terminal = state in REMOTE_TERMINAL_STATES
        raw_result = execution.get("raw_result")
        raw_task = (
            raw_result.get("task")
            if isinstance(raw_result, dict) and isinstance(raw_result.get("task"), dict)
            else {}
        )
        return self._update_task(
            task_id,
            dispatch_state="COMPLETED" if terminal else state,
            business_state=state if terminal else "",
            remote_execution_id=str(
                execution.get("remote_execution_id") or current.get("remote_execution_id") or ""
            ),
            actual_version=str(
                execution.get("actual_version")
                or execution.get("project_version")
                or current.get("actual_version")
                or ""
            ),
            project_version=str(
                execution.get("project_version") or current.get("project_version") or ""
            ),
            function_id=str(execution.get("function_id") or current.get("function_id") or ""),
            version_status=str(
                execution.get("version_status") or current.get("version_status") or ""
            ),
            last_known_remote_state=state,
            reconciliation_required=state in {"PAUSE_REQUESTED", "RESULT_UNKNOWN"},
            connectivity=connectivity,
            error_code=str(execution.get("error_code") or ""),
            message=str(execution.get("result_summary") or execution.get("message") or ""),
            risk_level=str(
                execution.get("risk_level")
                or raw_task.get("risk_level")
                or current.get("risk_level")
                or ""
            ),
            recommended_action=str(
                execution.get("recommended_action")
                or raw_task.get("recommended_action")
                or current.get("recommended_action")
                or ""
            ),
        )

    @staticmethod
    def _validate_health(machine: dict, health: dict) -> tuple[str, str, str]:
        if str(health.get("machine_id") or "").casefold() != machine["machine_id"].casefold():
            return "BLOCKED", "MACHINE_ID_MISMATCH", "SSH 目标返回了其他执行机编号"
        if str(health.get("role") or "") != machine["expected_role"]:
            return "BLOCKED", "MACHINE_ROLE_MISMATCH", "SSH 目标不是已登记的执行机角色"
        project_version = str(health.get("project_version") or "")
        if project_version not in machine["allowed_versions"]:
            return "BLOCKED", "PROJECT_VERSION_MISMATCH", "执行机项目版本不在允许范围"
        health_state = str(health.get("health") or health.get("status") or "").upper()
        if health.get("healthy") is False or health_state in {"DEGRADED", "FAILED", "BLOCKED"}:
            return "BLOCKED", "EXECUTOR_UNHEALTHY", "执行机健康检查未通过"
        if bool(health.get("paused")) or health_state == "PAUSED":
            return "PAUSED", "", ""
        return "READY", "", ""

    @staticmethod
    def _normalize_machine(values: dict) -> dict:
        required = (
            "machine_id",
            "ssh_host",
            "ssh_user",
            "working_directory",
            "allowed_versions",
        )
        missing = [name for name in required if not values.get(name)]
        if missing:
            raise ServiceError("INVALID_MACHINE", f"执行机配置缺少字段: {', '.join(missing)}")
        versions = values.get("allowed_versions")
        if not isinstance(versions, list) or not all(str(value).strip() for value in versions):
            raise ServiceError("INVALID_MACHINE", "允许版本必须是非空字符串列表")
        identity_file = str(values.get("identity_file") or "").strip()
        if identity_file and not Path(identity_file).is_absolute():
            raise ServiceError("INVALID_MACHINE", "SSH 私钥文件必须使用经理机绝对路径")
        return {
            "manager_id": (str(values.get("manager_id") or "").strip() or "MANAGER").upper(),
            "machine_id": str(values["machine_id"]).strip().upper(),
            "host_alias": str(values.get("host_alias") or values["ssh_host"]).strip(),
            "ssh_host": str(values["ssh_host"]).strip(),
            "ssh_port": int(values.get("ssh_port") or 22),
            "ssh_user": str(values["ssh_user"]).strip(),
            "working_directory": str(values["working_directory"]).strip(),
            "python_executable": str(values.get("python_executable") or "python").strip(),
            "identity_file": identity_file,
            "allowed_versions": sorted({str(value).strip() for value in versions}),
            "expected_role": str(values.get("expected_role") or EXPECTED_EXECUTOR_ROLE),
            "connect_timeout_seconds": int(values.get("connect_timeout_seconds") or 10),
        }

    @staticmethod
    def _machine_definition(machine: dict) -> dict:
        keys = (
            "manager_id",
            "machine_id",
            "host_alias",
            "ssh_host",
            "ssh_port",
            "ssh_user",
            "working_directory",
            "python_executable",
            "identity_file",
            "allowed_versions",
            "expected_role",
            "connect_timeout_seconds",
        )
        return {key: machine.get(key) for key in keys}

    @staticmethod
    def _endpoint_key(machine: dict) -> tuple[str, int, str]:
        return (
            str(machine["ssh_host"]).lower(),
            int(machine["ssh_port"]),
            str(machine["ssh_user"]).lower(),
        )

    @staticmethod
    def _normalize_task_request(request: dict) -> dict:
        required = ("task_id", "machine_id", "account_slot", "capability")
        missing = [name for name in required if not request.get(name)]
        if missing:
            raise ServiceError("INVALID_TASK", f"远程任务缺少字段: {', '.join(missing)}")
        parameters = request.get("parameters") or {}
        authorization = request.get("authorization") or {}
        if not isinstance(parameters, dict) or not isinstance(authorization, dict):
            raise ServiceError("INVALID_TASK", "任务参数和授权信息必须是 JSON 对象")
        ManagerService._validate_no_private_credentials(parameters)
        ManagerService._validate_no_private_credentials(authorization)
        function_id = str(request.get("function_id") or "").strip()
        allowed_function_versions = [
            str(value).strip() for value in request.get("allowed_function_versions") or []
        ]
        if function_id and not allowed_function_versions:
            raise ServiceError(
                "INVALID_TASK",
                "指定function_id时必须给出allowed_function_versions",
            )
        if any(not value for value in allowed_function_versions):
            raise ServiceError("INVALID_TASK", "功能包允许版本不能包含空值")
        normalized = {
            "task_id": str(request["task_id"]).strip(),
            "machine_id": str(request["machine_id"]).strip().upper(),
            "account_slot": str(request["account_slot"]).strip(),
            "capability": str(request["capability"]).strip(),
            "operation": str(request.get("operation") or "").strip(),
            "platform": str(request.get("platform") or "xiaohongshu").strip(),
            "request_summary": str(request.get("request_summary") or "经理机下发任务").strip(),
            "source_type": str(request.get("source_type") or "manager").strip(),
            "target_type": str(request.get("target_type") or "").strip(),
            "target_id": str(request.get("target_id") or "").strip(),
            "function_id": function_id,
            "allowed_function_versions": allowed_function_versions,
            "parameters": parameters,
            "authorization": authorization,
            "allowed_versions": [
                str(value).strip() for value in request.get("allowed_versions") or []
            ],
            "write_intent": bool(request.get("write_intent")),
            "timeout_seconds": float(request.get("timeout_seconds") or 300),
        }
        json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return normalized

    @staticmethod
    def _validate_no_private_credentials(values: dict) -> None:
        forbidden_parts = {
            "password",
            "passwd",
            "cookie",
            "secret",
            "privatekey",
            "accesstoken",
            "refreshtoken",
            "sessiontoken",
            "credential",
        }

        def walk(value: object) -> None:
            if isinstance(value, dict):
                for key, nested in value.items():
                    normalized = "".join(
                        character for character in str(key).lower() if character.isalnum()
                    )
                    if any(part in normalized for part in forbidden_parts):
                        raise ServiceError(
                            "PRIVATE_CREDENTIAL_NOT_ALLOWED",
                            "任务参数和授权信息不得包含密码、Cookie、令牌或私钥",
                        )
                    walk(nested)
            elif isinstance(value, list):
                for nested in value:
                    walk(nested)

        walk(values)

    def _task_envelope(self, task: dict, machine: dict) -> dict:
        request = task["request"]
        return {
            "schema_version": 1,
            "task_id": task["task_id"],
            "dispatch_id": task["dispatch_id"],
            "machine_id": machine["machine_id"],
            "account_slot": request["account_slot"],
            "capability": request["capability"],
            "operation": request["operation"],
            "platform": request["platform"],
            "request_summary": request["request_summary"],
            "source_type": request["source_type"],
            "target_type": request["target_type"],
            "target_id": request["target_id"],
            "manager_id": machine["manager_id"],
            "function_id": request["function_id"],
            "allowed_function_versions": request["allowed_function_versions"],
            "parameters": request["parameters"],
            "authorization": request["authorization"],
            "allowed_versions": request["allowed_versions"] or machine["allowed_versions"],
            "write_intent": request["write_intent"],
        }

    @staticmethod
    def _normalize_version_policy_request(request: dict) -> dict:
        required = ("machine_id", "function_id", "version", "status")
        missing = [name for name in required if not str(request.get(name) or "").strip()]
        if missing:
            raise ServiceError(
                "INVALID_VERSION_POLICY",
                f"版本策略缺少字段: {', '.join(missing)}",
            )
        status_aliases = {
            "FORMAL": "FORMAL",
            "TRIAL": "TRIAL",
            "DISABLED": "DISABLED",
            "正式": "FORMAL",
            "试用": "TRIAL",
            "停用": "DISABLED",
        }
        status = status_aliases.get(str(request["status"]).strip().upper())
        if not status:
            raise ServiceError("INVALID_VERSION_POLICY", "版本状态必须是正式、试用或停用")
        scope_value = request.get("scope")
        scope: dict[str, list[str]] | None = None
        if scope_value is not None:
            if not isinstance(scope_value, dict):
                raise ServiceError("INVALID_VERSION_POLICY", "版本启用范围必须是 JSON 对象")
            scope = {}
            aliases = {
                "manager_ids": ("manager_ids", "managers"),
                "machine_ids": ("machine_ids", "devices"),
                "account_slots": ("account_slots", "accounts"),
                "allowed_actions": ("allowed_actions", "actions"),
            }
            for target, names in aliases.items():
                raw = next((scope_value[name] for name in names if name in scope_value), None)
                if target == "allowed_actions" and raw is None:
                    raw = ["*"] if status != "DISABLED" else []
                if not isinstance(raw, list):
                    raise ServiceError(
                        "INVALID_VERSION_POLICY",
                        f"scope.{target}必须是数组",
                    )
                normalized = sorted({str(item).strip() for item in raw if str(item).strip()})
                if not normalized and status != "DISABLED":
                    raise ServiceError(
                        "INVALID_VERSION_POLICY",
                        f"scope.{target}不能为空",
                    )
                scope[target] = normalized
        elif status != "DISABLED":
            raise ServiceError(
                "INVALID_VERSION_POLICY",
                "正式或试用版本必须明确经理、设备和账号范围",
            )
        return {
            "machine_id": str(request["machine_id"]).strip().upper(),
            "function_id": str(request["function_id"]).strip(),
            "version": str(request["version"]).strip(),
            "status": status,
            "scope": scope,
            "set_as_stable": bool(request.get("set_as_stable")),
        }

    def _cache_version(self, machine_id: str, item: dict) -> dict:
        function_id = str(item.get("function_id") or "").strip()
        version = str(item.get("version") or "").strip()
        if not function_id or not version:
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机版本登记缺少功能编号或版本")
        record = {
            **item,
            "record_id": f"version:{machine_id}:{function_id}:{version}",
            "created_at": str(item.get("installed_at") or utc_now()),
            "data_status": "observed",
            "missing_reason": "",
            "machine_id": machine_id,
            "function_id": function_id,
            "version": version,
            "installed_version": version,
            "actual_version": version,
            "observed_at": utc_now(),
        }
        return self.store.put("versions", f"{machine_id}:{function_id}:{version}", record)

    def _cached_versions(self, machine_id: str, function_id: str = "") -> list[dict]:
        values = [
            item
            for item in self.store.list("versions")
            if item.get("machine_id") == machine_id
            and (not function_id or item.get("function_id") == function_id)
        ]
        values.sort(key=lambda item: (str(item.get("function_id")), str(item.get("version"))))
        return values

    @staticmethod
    def _remote_transfer_projection(remote: dict) -> dict:
        return {
            "actual_version": str(remote.get("actual_version") or ""),
            "installed_path": str(remote.get("installed_path") or ""),
            "function_id": str(remote.get("function_id") or ""),
            "version_status": str(remote.get("version_status") or ""),
            "stable_version": str(remote.get("stable_version") or ""),
            "enabled_scope": remote.get("enabled_scope")
            if isinstance(remote.get("enabled_scope"), dict)
            else {},
            "business_module": str(remote.get("business_module") or ""),
        }

    def _cache_transfer_version(self, machine_id: str, projection: dict) -> None:
        function_id = str(projection.get("function_id") or "")
        version = str(projection.get("actual_version") or "")
        if not function_id or not version:
            return
        self._cache_version(
            machine_id,
            {
                "function_id": function_id,
                "version": version,
                "status": str(projection.get("version_status") or "DISABLED"),
                "scope": projection.get("enabled_scope") or {},
                "stable": str(projection.get("stable_version") or "") == version,
                "installed_path": str(projection.get("installed_path") or ""),
                "business_module": str(projection.get("business_module") or ""),
            },
        )

    @staticmethod
    def _normalize_transfer_request(request: dict) -> dict:
        required = ("transfer_id", "machine_id", "transfer_type", "source_path")
        missing = [name for name in required if not request.get(name)]
        if missing:
            raise ServiceError("INVALID_TRANSFER", f"传输请求缺少字段: {', '.join(missing)}")
        transfer_type = str(request["transfer_type"])
        if transfer_type not in {"task_package", "function_package", "config"}:
            raise ServiceError("INVALID_TRANSFER", "传输类型必须是任务包、功能包或配置")
        source = Path(str(request["source_path"])).expanduser()
        if not source.is_absolute():
            raise ServiceError("INVALID_TRANSFER", "传输源路径必须是绝对路径")
        source = source.resolve()
        if not source.exists():
            raise ServiceError("TRANSFER_SOURCE_MISSING", "传输源文件或目录不存在", 404)
        return {
            "transfer_id": str(request["transfer_id"]).strip(),
            "machine_id": str(request["machine_id"]).strip().upper(),
            "transfer_type": transfer_type,
            "source_path": str(source),
            "allowed_versions": [
                str(value).strip() for value in request.get("allowed_versions") or []
            ],
            "timeout_seconds": float(request.get("timeout_seconds") or 600),
        }

    @staticmethod
    def _join_remote_path(root: str, name: str) -> str:
        separator = "\\" if "\\" in root else "/"
        return root.rstrip("/\\") + separator + name

    def _get_machine(self, machine_id: str) -> dict:
        machine = self.store.get("machines", str(machine_id).strip().upper())
        if machine is None:
            raise ServiceError("MACHINE_NOT_FOUND", "执行机未注册", 404)
        return machine

    def _get_task(self, task_id: str) -> dict:
        task = self.store.get("tasks", task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "经理机任务不存在", 404)
        return task

    def _update_machine(self, machine_id: str, **values) -> dict:
        machine_id = str(machine_id).strip().upper()
        values.setdefault("updated_at", utc_now())
        status = str(values.get("status") or "")
        if "ssh_state" not in values:
            if status in {"READY", "PAUSED", "BLOCKED"}:
                values["ssh_state"] = "ONLINE"
            elif status == "OFFLINE":
                values["ssh_state"] = "OFFLINE"
        updated = self.store.update(
            "machines",
            machine_id,
            lambda current: {**current, **values},
        )
        if updated is None:
            raise ServiceError("MACHINE_NOT_FOUND", "执行机未注册", 404)
        return updated

    def _update_task(self, task_id: str, **values) -> dict:
        values["updated_at"] = utc_now()

        def mutate(current: dict) -> dict:
            updated = {**current, **values}
            updated["state"] = updated.get("business_state") or updated.get("dispatch_state")
            updated["result_summary"] = updated.get("message") or ""
            return updated

        updated = self.store.update(
            "tasks",
            task_id,
            mutate,
        )
        if updated is None:
            raise ServiceError("TASK_NOT_FOUND", "经理机任务不存在", 404)
        return updated

    def _update_transfer(self, transfer_id: str, **values) -> dict:
        values["updated_at"] = utc_now()

        def mutate(current: dict) -> dict:
            updated = {**current, **values}
            updated["remote_path"] = (
                updated.get("installed_path") or updated.get("remote_staging_path") or ""
            )
            return updated

        updated = self.store.update(
            "transfers",
            transfer_id,
            mutate,
        )
        if updated is None:
            raise ServiceError("TRANSFER_NOT_FOUND", "传输记录不存在", 404)
        return updated
