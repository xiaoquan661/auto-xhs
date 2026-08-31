"""经理机通过 SSH 调用的执行机持久化服务。"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import socket
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .run_lock import RunLock
except ImportError:
    from run_lock import RunLock


STATE_SCHEMA_VERSION = 1
TRANSFER_TYPES = {"task_package", "function_package", "config"}
TASK_FINAL_STATES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "CANCELLED",
    "BLOCKED",
    "RESULT_UNKNOWN",
}
LOCAL_TASK_FINAL_STATES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "CANCELLED",
    "RESULT_UNKNOWN",
}
SUPPORTED_MANIFEST_VERSIONS = {
    "1",
    "manager_transfer_manifest_v1",
    "xhs_manager_transfer_manifest_v1",
}
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_PROCESS_LOCK = threading.RLock()
T = TypeVar("T")


def utc_now() -> str:
    """返回可排序的 UTC 时间。"""

    return datetime.now(UTC).isoformat()


def manager_worker_root() -> Path:
    """解析执行机协议状态根目录。"""

    override = os.getenv("XHS_MANAGER_WORKER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".xhs" / "auto-xhs" / "manager-worker"


class WorkerServiceError(RuntimeError):
    """稳定的执行机协议错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error_code": self.code,
            "message": self.message,
            "error": {"code": self.code, "message": self.message},
        }


def _default_config(root: Path) -> dict[str, Any]:
    machine_id = (os.getenv("COMPUTERNAME") or socket.gethostname() or "EXECUTOR").strip()
    return {
        "machine_id": machine_id.upper(),
        "role": "executor",
        "project_version": "0.1.0",
        "package_root": str((root / "packages").resolve()),
        "task_inbox_root": str((root / "task-inbox").resolve()),
    }


def _default_state(root: Path) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "config": _default_config(root),
        "machine": {"paused": False, "updated_at": None},
        "executions": {},
        "task_index": {},
        "dispatch_index": {},
        "transfers": {},
        "function_versions": {},
        "results": [],
        "next_sequence": 1,
    }


class _JsonWorkerStore:
    """使用单个 JSON 文件和 RunLock 提供原子事务。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else manager_worker_root()
        self.path = self.root / "worker-state.json"
        self.lock_path = self.root / "worker-state.lock"

    def snapshot(self) -> dict[str, Any]:
        return self._transaction(lambda state: copy.deepcopy(state), write=False)

    def mutate(self, callback: Callable[[dict[str, Any]], T]) -> T:
        return self._transaction(callback, write=True)

    def _transaction(self, callback: Callable[[dict[str, Any]], T], *, write: bool) -> T:
        self.root.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            lock = RunLock(str(self.lock_path))
            if not lock.acquire(timeout=10):
                raise WorkerServiceError("STATE_BUSY", "执行机状态正在被其他进程使用")
            try:
                state = self._read_state()
                result = callback(state)
                if write:
                    self._write_state(state)
                return result
            finally:
                lock.release()

    def _read_state(self) -> dict[str, Any]:
        if not self.path.exists():
            return _default_state(self.root)
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkerServiceError("STATE_UNREADABLE", "执行机状态文件无法读取") from exc
        if state.get("schema_version") != STATE_SCHEMA_VERSION:
            raise WorkerServiceError("STATE_VERSION_UNSUPPORTED", "执行机状态版本不兼容")
        defaults = _default_state(self.root)
        for key in (
            "config",
            "machine",
            "executions",
            "task_index",
            "dispatch_index",
            "transfers",
            "function_versions",
            "results",
            "next_sequence",
        ):
            state.setdefault(key, copy.deepcopy(defaults[key]))
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)


def _default_account_summary() -> list[dict[str, Any]]:
    """只读取账号注册信息，不连接 Bridge 或真实账号。"""

    try:
        try:
            from .account_manager import list_accounts
        except ImportError:
            from account_manager import list_accounts

        return [
            {
                "name": item.name,
                "platform": item.platform,
                "profile_mode": item.profile_mode,
                "paired": bool(item.extension_instance_id),
            }
            for item in list_accounts()
        ]
    except Exception as exc:
        raise WorkerServiceError("ACCOUNT_SUMMARY_UNAVAILABLE", "账号槽位摘要读取失败") from exc


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _request_values(
    request: dict[str, Any] | None,
    values: dict[str, Any],
) -> dict[str, Any]:
    merged = dict(request or {})
    merged.update(values)
    return merged


def _required_text(values: dict[str, Any], name: str, label: str) -> str:
    value = str(values.get(name) or "").strip()
    if not value:
        raise WorkerServiceError("INVALID_REQUEST", f"{label}不能为空")
    return value


def _safe_component(value: str, label: str) -> str:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise WorkerServiceError(
            "INVALID_REQUEST",
            f"{label}只能包含字母、数字、点、下划线或连字符",
        )
    return value


def _allowed_versions(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise WorkerServiceError("INVALID_REQUEST", "allowed_versions必须是非空数组")
    versions = [str(item).strip() for item in value]
    if any(not item for item in versions):
        raise WorkerServiceError("INVALID_REQUEST", "allowed_versions不能包含空版本")
    return versions


class ManagerWorkerService:
    """执行机固定入口背后的本地服务。"""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        account_summary_reader: Callable[[], list[dict[str, Any]]] | None = None,
        local_api_url: str | None = None,
        local_control: Callable[[str, str | None], dict[str, Any]] | None = None,
        local_service_probe: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self._store = _JsonWorkerStore(root)
        self.root = self._store.root
        self._executor = executor
        self._account_summary_reader = account_summary_reader or _default_account_summary
        self._local_control = local_control
        self._local_service_probe = local_service_probe
        self._local_api_url = (
            local_api_url or os.getenv("XHS_LOCAL_API_URL") or "http://127.0.0.1:8765/api/v1"
        ).rstrip("/")

    def configure(
        self,
        request: dict[str, Any] | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        requested = _request_values(request, values)
        if isinstance(requested.get("config"), dict):
            requested = {**requested, **requested["config"]}

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            current = dict(state["config"])
            updated = dict(current)
            for name in (
                "machine_id",
                "role",
                "project_version",
                "package_root",
                "task_inbox_root",
            ):
                if name in requested:
                    updated[name] = str(requested[name]).strip()
            for name in ("machine_id", "role", "project_version"):
                if not updated.get(name):
                    raise WorkerServiceError("INVALID_CONFIG", f"config.{name}不能为空")
            if updated["role"] != "executor":
                raise WorkerServiceError("INVALID_CONFIG", "执行机role必须是executor")
            updated["machine_id"] = updated["machine_id"].upper()
            for name in ("package_root", "task_inbox_root"):
                if not updated.get(name):
                    raise WorkerServiceError("INVALID_CONFIG", f"config.{name}不能为空")
                updated[name] = str(Path(updated[name]).expanduser().resolve())
            if state["executions"] and updated["machine_id"] != current["machine_id"]:
                raise WorkerServiceError("CONFIG_IN_USE", "已有任务时不能修改machine_id")
            if updated["project_version"] != current["project_version"] and any(
                execution.get("state") not in TASK_FINAL_STATES
                for execution in state["executions"].values()
            ):
                raise WorkerServiceError(
                    "CONFIG_IN_USE",
                    "存在未结束任务时不能修改project_version",
                )
            if state["transfers"] and any(
                updated[name] != current[name] for name in ("package_root", "task_inbox_root")
            ):
                raise WorkerServiceError("CONFIG_IN_USE", "已有传输记录时不能修改包目录")
            state["config"] = updated
            return copy.deepcopy(updated)

        return {"success": True, "config": self._store.mutate(mutate)}

    def health(self) -> dict[str, Any]:
        """只读返回执行机与账号槽位摘要。"""

        state = self._store.snapshot()
        config = state["config"]
        health = "PAUSED" if state["machine"]["paused"] else "READY"
        summary_error = ""
        try:
            slots = self._account_summary_reader()
        except WorkerServiceError as exc:
            slots = []
            summary_error = exc.message
            health = "DEGRADED"
        except Exception:
            slots = []
            summary_error = "账号槽位摘要读取失败"
            health = "DEGRADED"
        local_service_error = ""
        try:
            if self._local_service_probe is not None:
                local_service = self._local_service_probe()
            elif self._local_control is not None:
                local_service = {"success": True, "mode": "injected"}
            else:
                local_service = self._local_api_request("GET", "/session")
            local_service_ready = local_service.get("success") is not False
            if not local_service_ready:
                local_service_error = "本地应用服务健康检查未通过"
        except Exception:
            local_service_ready = False
            local_service_error = "本地应用服务不可用"
        if not local_service_ready:
            health = "DEGRADED"
        health_payload = {
            "machine_id": config["machine_id"],
            "role": config["role"],
            "project_version": config["project_version"],
            "paused": bool(state["machine"]["paused"]),
            "healthy": health != "DEGRADED",
            "status": health,
            "local_service": {
                "ready": local_service_ready,
                "url": self._local_api_url,
                "error": local_service_error,
            },
            "account_slots": {
                "count": len(slots),
                "items": copy.deepcopy(slots),
                "error": summary_error,
            },
        }
        return {
            "success": True,
            "health": health_payload,
        }

    def list_function_versions(
        self,
        request: dict[str, Any] | None = None,
        *,
        function_id: str | None = None,
    ) -> dict[str, Any]:
        requested = _request_values(request, {"function_id": function_id} if function_id else {})
        selected_function = str(requested.get("function_id") or "").strip()
        if selected_function:
            selected_function = _safe_component(selected_function, "function_id")
        state = self._store.snapshot()
        items: list[dict[str, Any]] = []
        functions: list[dict[str, Any]] = []
        for identifier, registry in state["function_versions"].items():
            if selected_function and identifier != selected_function:
                continue
            stable_version = str(registry.get("stable_version") or "")
            functions.append(
                {
                    "function_id": identifier,
                    "business_module": registry.get("business_module", ""),
                    "function_name": registry.get("function_name", ""),
                    "stable_version": stable_version,
                }
            )
            for version, entry in registry.get("versions", {}).items():
                items.append(
                    {
                        **copy.deepcopy(entry),
                        "function_id": identifier,
                        "version": version,
                        "stable": version == stable_version,
                    }
                )
        items.sort(key=lambda item: (item["function_id"], self._version_sort_key(item["version"])))
        functions.sort(key=lambda item: item["function_id"])
        return {"success": True, "functions": functions, "versions": items}

    def set_function_version_policy(
        self,
        request: dict[str, Any] | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        requested = _request_values(request, values)
        function_id = _safe_component(
            _required_text(requested, "function_id", "function_id"),
            "function_id",
        )
        version = _safe_component(_required_text(requested, "version", "version"), "version")
        status_aliases = {
            "FORMAL": "FORMAL",
            "TRIAL": "TRIAL",
            "DISABLED": "DISABLED",
            "正式": "FORMAL",
            "试用": "TRIAL",
            "停用": "DISABLED",
        }
        status = status_aliases.get(str(requested.get("status") or "").strip().upper())
        if not status:
            raise WorkerServiceError("INVALID_VERSION_STATUS", "版本状态必须是正式、试用或停用")
        scope_value = requested.get("scope")
        if scope_value is not None and not isinstance(scope_value, dict):
            raise WorkerServiceError("INVALID_VERSION_SCOPE", "版本启用范围必须是JSON对象")
        set_as_stable = bool(requested.get("set_as_stable"))

        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            registry = state["function_versions"].get(function_id)
            if not isinstance(registry, dict):
                raise WorkerServiceError("FUNCTION_NOT_INSTALLED", "执行机未安装该功能包")
            entry = registry.get("versions", {}).get(version)
            if not isinstance(entry, dict):
                raise WorkerServiceError("FUNCTION_VERSION_NOT_INSTALLED", "执行机未安装该功能版本")
            if status == "DISABLED" and registry.get("stable_version") == version:
                raise WorkerServiceError(
                    "STABLE_VERSION_REQUIRED",
                    "请先切换稳定版本再停用当前稳定版",
                )
            if status != "FORMAL" and registry.get("stable_version") == version:
                raise WorkerServiceError(
                    "STABLE_VERSION_STATUS_REQUIRED",
                    "稳定版本必须保持正式状态",
                )
            if status == "DISABLED" and any(
                execution.get("function_id") == function_id
                and execution.get("actual_version") == version
                and execution.get("state") not in TASK_FINAL_STATES
                for execution in state["executions"].values()
            ):
                raise WorkerServiceError(
                    "FUNCTION_VERSION_IN_USE",
                    "该版本仍有在途任务，暂不能停用",
                )
            compatible = entry.get("compatible_project_versions") or ["*"]
            project_version = state["config"]["project_version"]
            if status != "DISABLED" and project_version not in compatible and "*" not in compatible:
                raise WorkerServiceError(
                    "FUNCTION_VERSION_INCOMPATIBLE",
                    "功能包版本与当前项目版本不兼容",
                )
            if set_as_stable and status != "FORMAL":
                raise WorkerServiceError("INVALID_STABLE_VERSION", "只有正式版本可以设为稳定版本")
            if status in {"FORMAL", "TRIAL"}:
                scope = self._normalize_version_scope(scope_value)
            elif scope_value is not None:
                scope = self._normalize_version_scope(scope_value, allow_empty=True)
            else:
                scope = copy.deepcopy(entry.get("scope") or self._empty_version_scope())
            desired = {
                "status": status,
                "scope": scope,
                "stable_version": version if set_as_stable else registry.get("stable_version", ""),
            }
            current = {
                "status": entry.get("status"),
                "scope": entry.get("scope"),
                "stable_version": registry.get("stable_version", ""),
            }
            if desired == current:
                result = {**copy.deepcopy(entry), "function_id": function_id, "version": version}
                result["stable"] = registry.get("stable_version") == version
                return result, True
            now = utc_now()
            entry["status"] = status
            entry["scope"] = scope
            entry["policy_updated_at"] = now
            if set_as_stable:
                registry["stable_version"] = version
            registry["updated_at"] = now
            result = {**copy.deepcopy(entry), "function_id": function_id, "version": version}
            result["stable"] = registry.get("stable_version") == version
            return result, False

        entry, idempotent = self._store.mutate(mutate)
        return {"success": True, "version": entry, "idempotent": idempotent}

    def accept_task(
        self,
        request: dict[str, Any] | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        requested = _request_values(request, values)
        task_id = _safe_component(_required_text(requested, "task_id", "task_id"), "task_id")
        dispatch_id = _safe_component(
            _required_text(requested, "dispatch_id", "dispatch_id"),
            "dispatch_id",
        )
        target_machine_id = str(
            requested.get("target_machine_id") or requested.get("machine_id") or ""
        ).strip()
        if not target_machine_id:
            raise WorkerServiceError("INVALID_REQUEST", "target_machine_id不能为空")
        allowed = _allowed_versions(requested.get("allowed_versions"))
        task_payload = requested.get("task") or requested.get("payload")
        if task_payload is None:
            task_payload = {
                key: copy.deepcopy(requested[key])
                for key in (
                    "source",
                    "account_slot",
                    "capability",
                    "request_summary",
                    "operation",
                    "parameters",
                    "source_type",
                    "source_event_id",
                    "parent_task_id",
                    "target_type",
                    "target_id",
                    "platform",
                    "authorization",
                    "write_intent",
                    "manager_id",
                    "function_id",
                    "allowed_function_versions",
                )
                if key in requested
            }
        if not isinstance(task_payload, dict) or not task_payload:
            raise WorkerServiceError("INVALID_REQUEST", "task必须是非空对象")
        contract = {
            "task_id": task_id,
            "dispatch_id": dispatch_id,
            "target_machine_id": target_machine_id.upper(),
            "allowed_versions": allowed,
            "task": copy.deepcopy(task_payload),
        }

        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            config = state["config"]
            if target_machine_id.casefold() != str(config["machine_id"]).casefold():
                raise WorkerServiceError("TARGET_MACHINE_MISMATCH", "任务目标执行机不匹配")
            if config["project_version"] not in allowed and "*" not in allowed:
                raise WorkerServiceError(
                    "PROJECT_VERSION_NOT_ALLOWED", "执行机项目版本不在允许范围"
                )
            task_match = state["task_index"].get(task_id)
            dispatch_match = state["dispatch_index"].get(dispatch_id)
            existing_id = task_match or dispatch_match
            if existing_id:
                existing = state["executions"][existing_id]
                if (
                    existing["task_id"] != task_id
                    or existing["dispatch_id"] != dispatch_id
                    or existing["accepted_request"] != contract
                ):
                    raise WorkerServiceError(
                        "TASK_IDEMPOTENCY_CONFLICT",
                        "task_id或dispatch_id已用于不同任务",
                    )
                return copy.deepcopy(existing), True
            selected_version = self._select_function_version(state, task_payload, config)
            now = utc_now()
            remote_execution_id = uuid.uuid4().hex
            execution = {
                "remote_execution_id": remote_execution_id,
                "task_id": task_id,
                "dispatch_id": dispatch_id,
                "machine_id": config["machine_id"],
                "project_version": config["project_version"],
                "function_id": selected_version["function_id"],
                "actual_version": selected_version["actual_version"],
                "version_status": selected_version["version_status"],
                "state": "ACCEPTED",
                "task": copy.deepcopy(task_payload),
                "accepted_request": contract,
                "accepted_at": now,
                "started_at": None,
                "finished_at": None,
                "updated_at": now,
                "business_result": {},
                "business_result_status": "missing",
                "business_result_missing_reason": "任务尚未结算",
                "item_results": [],
                "item_results_status": "missing",
                "item_results_missing_reason": "任务尚未结算",
                "evidence_index": {},
                "incremental_data": [],
                "incremental_data_status": "missing",
                "incremental_data_missing_reason": "任务尚未结算",
                "error_code": "",
                "error_classification": "",
                "manual_action_required": False,
                "recommended_action": "",
                "risk_level": "",
                "message": "任务已接收",
                "reconciliation_required": False,
                "paused_by_machine": False,
            }
            state["executions"][remote_execution_id] = execution
            state["task_index"][task_id] = remote_execution_id
            state["dispatch_index"][dispatch_id] = remote_execution_id
            return copy.deepcopy(execution), False

        execution, idempotent = self._store.mutate(mutate)
        return self._execution_response(execution, idempotent=idempotent)

    def task_status(
        self,
        task_id: str | None = None,
        *,
        remote_execution_id: str | None = None,
    ) -> dict[str, Any]:
        state = self._store.snapshot()
        execution = self._find_execution(state, task_id, remote_execution_id)
        if execution["state"] in {
            "RUNNING",
            "PAUSE_REQUESTED",
            "RECONCILIATION_REQUIRED",
        }:
            execution = self._reconcile_execution_status(execution)
        return self._execution_response(execution)

    def run_task(
        self,
        task_id: str | None = None,
        *,
        remote_execution_id: str | None = None,
        executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        def start(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            execution = self._find_execution(state, task_id, remote_execution_id)
            if execution["state"] in TASK_FINAL_STATES | {
                "RUNNING",
                "PAUSED",
                "PAUSE_REQUESTED",
                "RECONCILIATION_REQUIRED",
            }:
                return copy.deepcopy(execution), False
            if execution["state"] != "ACCEPTED":
                raise WorkerServiceError("INVALID_TASK_STATE", "当前任务状态不能执行")
            if state["machine"]["paused"]:
                return copy.deepcopy(execution), False
            execution["state"] = "RUNNING"
            execution["started_at"] = utc_now()
            execution["updated_at"] = execution["started_at"]
            execution["message"] = "任务正在执行"
            return copy.deepcopy(execution), True

        execution, should_execute = self._store.mutate(start)
        if not should_execute:
            if execution["state"] == "ACCEPTED":
                execution = copy.deepcopy(execution)
                execution["blocked_reason"] = "执行机已暂停"
            return self._execution_response(execution, idempotent=True)
        execute = executor or self._executor or self._execute_via_local_api
        payload = copy.deepcopy(execution["task"])
        payload.setdefault("task_id", execution["task_id"])
        payload.setdefault("dispatch_id", execution["dispatch_id"])
        payload.setdefault("remote_execution_id", execution["remote_execution_id"])
        payload.setdefault("project_version", execution["project_version"])
        payload.setdefault("function_id", execution.get("function_id", ""))
        payload.setdefault("actual_version", execution.get("actual_version", ""))
        payload.setdefault("version_status", execution.get("version_status", "PROJECT"))
        try:
            raw_outcome = execute(payload)
            if not isinstance(raw_outcome, dict):
                raise TypeError("executor必须返回JSON对象")
            outcome = self._normalize_outcome(raw_outcome)
        except Exception as exc:
            outcome = {
                "state": "RESULT_UNKNOWN" if getattr(exc, "result_unknown", False) else "FAILED",
                "business_result": {},
                "business_result_status": "failed",
                "business_result_missing_reason": "任务执行异常，未取得业务结果",
                "item_results": [],
                "item_results_status": "failed",
                "item_results_missing_reason": "任务执行异常，未取得逐项结果",
                "evidence_index": {},
                "incremental_data": [],
                "incremental_data_status": "failed",
                "incremental_data_missing_reason": "任务执行异常，未取得增量数据",
                "error_code": getattr(exc, "code", "EXECUTION_ERROR"),
                "error_classification": (
                    "RESULT_UNKNOWN"
                    if getattr(exc, "result_unknown", False)
                    else "EXECUTION_FAILED"
                ),
                "manual_action_required": True,
                "recommended_action": "检查执行机任务记录并完成只读对账",
                "risk_level": "",
                "message": str(exc) or "任务执行失败",
                "raw_result": {},
            }

        def finalize(state: dict[str, Any]) -> dict[str, Any]:
            current = self._find_execution(
                state,
                execution["task_id"],
                execution["remote_execution_id"],
            )
            now = utc_now()
            if current["state"] == "PAUSE_REQUESTED":
                current["state"] = "RECONCILIATION_REQUIRED"
                current["reconciliation_required"] = True
                current["observed_executor_outcome"] = copy.deepcopy(outcome)
                current["message"] = "任务在暂停请求期间结束，需要回读确认"
                current["error_code"] = "PAUSE_RECONCILIATION_REQUIRED"
                current["error_classification"] = "RECONCILIATION_REQUIRED"
                current["manual_action_required"] = True
                current["finished_at"] = now
                current["updated_at"] = now
                self._append_result(state, current)
                return copy.deepcopy(current)
            if current["state"] != "RUNNING":
                return copy.deepcopy(current)
            current.update(
                {
                    "state": outcome["state"],
                    "business_result": outcome["business_result"],
                    "business_result_status": outcome["business_result_status"],
                    "business_result_missing_reason": outcome["business_result_missing_reason"],
                    "item_results": outcome["item_results"],
                    "item_results_status": outcome["item_results_status"],
                    "item_results_missing_reason": outcome["item_results_missing_reason"],
                    "evidence_index": outcome["evidence_index"],
                    "incremental_data": outcome["incremental_data"],
                    "incremental_data_status": outcome["incremental_data_status"],
                    "incremental_data_missing_reason": outcome["incremental_data_missing_reason"],
                    "error_code": outcome["error_code"],
                    "error_classification": outcome["error_classification"],
                    "manual_action_required": outcome["manual_action_required"],
                    "recommended_action": outcome["recommended_action"],
                    "risk_level": outcome["risk_level"],
                    "message": outcome["message"],
                    "raw_result": outcome["raw_result"],
                    "finished_at": now,
                    "updated_at": now,
                }
            )
            self._append_result(state, current)
            return copy.deepcopy(current)

        return self._execution_response(self._store.mutate(finalize))

    def pause_task(
        self,
        task_id: str | None = None,
        *,
        remote_execution_id: str | None = None,
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
            execution = self._find_execution(state, task_id, remote_execution_id)
            if (
                execution["state"]
                in {
                    "PAUSED",
                    "PAUSE_REQUESTED",
                    "RECONCILIATION_REQUIRED",
                }
                | TASK_FINAL_STATES
            ):
                return copy.deepcopy(execution), True, False
            requested_running_pause = False
            if execution["state"] == "ACCEPTED":
                execution["state"] = "PAUSED"
                execution["message"] = "任务已暂停"
            elif execution["state"] == "RUNNING":
                execution["state"] = "PAUSE_REQUESTED"
                execution["reconciliation_required"] = True
                execution["error_classification"] = "RECONCILIATION_REQUIRED"
                execution["manual_action_required"] = True
                execution["message"] = "已记录暂停请求，等待在途步骤结束后对账"
                requested_running_pause = True
            else:
                raise WorkerServiceError("INVALID_TASK_STATE", "当前任务状态不能暂停")
            execution["updated_at"] = utc_now()
            self._append_result(state, execution)
            return copy.deepcopy(execution), False, requested_running_pause

        execution, idempotent, requested_running_pause = self._store.mutate(mutate)
        response = self._execution_response(execution, idempotent=idempotent)
        local_task_id = str(execution.get("local_task_id") or "")
        if requested_running_pause and local_task_id:
            response["execution"]["local_cancel"] = self._sync_local_control(
                "cancel-task",
                local_task_id,
            )
        return response

    def resume_task(
        self,
        task_id: str | None = None,
        *,
        remote_execution_id: str | None = None,
    ) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            execution = self._find_execution(state, task_id, remote_execution_id)
            if execution["state"] == "PAUSED":
                if state["machine"]["paused"]:
                    execution["message"] = "执行机仍处于暂停状态"
                    return copy.deepcopy(execution), True
                execution["state"] = "ACCEPTED"
                execution["paused_by_machine"] = False
                execution["message"] = "任务已恢复，可继续执行"
                execution["updated_at"] = utc_now()
                self._append_result(state, execution)
                return copy.deepcopy(execution), False
            if execution["state"] == "PAUSE_REQUESTED":
                execution["state"] = "RECONCILIATION_REQUIRED"
                execution["reconciliation_required"] = True
                execution["message"] = "在途任务必须先完成结果对账"
                execution["updated_at"] = utc_now()
                self._append_result(state, execution)
                return copy.deepcopy(execution), False
            return copy.deepcopy(execution), True

        execution, idempotent = self._store.mutate(mutate)
        return self._execution_response(execution, idempotent=idempotent)

    def pause_machine(self) -> dict[str, Any]:
        local_sync = self._sync_local_control("pause-machine")

        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
            already_paused = bool(state["machine"]["paused"])
            state["machine"]["paused"] = True
            state["machine"]["updated_at"] = utc_now()
            affected: list[dict[str, Any]] = []
            if not already_paused:
                for execution in state["executions"].values():
                    if execution["state"] == "ACCEPTED":
                        execution["state"] = "PAUSED"
                        execution["paused_by_machine"] = True
                        execution["message"] = "任务随执行机暂停"
                    elif execution["state"] == "RUNNING":
                        execution["state"] = "PAUSE_REQUESTED"
                        execution["paused_by_machine"] = True
                        execution["reconciliation_required"] = True
                        execution["error_classification"] = "RECONCILIATION_REQUIRED"
                        execution["manual_action_required"] = True
                        execution["message"] = "执行机已暂停，在途任务等待对账"
                    else:
                        continue
                    execution["updated_at"] = utc_now()
                    self._append_result(state, execution)
                    affected.append(copy.deepcopy(execution))
            return copy.deepcopy(state["machine"]), affected, already_paused

        machine, affected, idempotent = self._store.mutate(mutate)
        cancellations = [
            {
                "remote_execution_id": item["remote_execution_id"],
                "local_cancel": self._sync_local_control(
                    "cancel-task",
                    str(item["local_task_id"]),
                ),
            }
            for item in affected
            if item["state"] == "PAUSE_REQUESTED" and item.get("local_task_id")
        ]
        return {
            "success": bool(local_sync.get("success")),
            "machine": {
                **machine,
                "idempotent": idempotent,
                "local_service_sync": local_sync,
            },
            "executions": [self._public_execution(item) for item in affected],
            "local_cancellations": cancellations,
        }

    def resume_machine(self) -> dict[str, Any]:
        local_sync = self._sync_local_control("resume-machine")

        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
            already_resumed = not bool(state["machine"]["paused"])
            state["machine"]["paused"] = False
            state["machine"]["updated_at"] = utc_now()
            affected: list[dict[str, Any]] = []
            if not already_resumed:
                for execution in state["executions"].values():
                    if execution["state"] == "PAUSED" and execution.get("paused_by_machine"):
                        execution["state"] = "ACCEPTED"
                        execution["paused_by_machine"] = False
                        execution["message"] = "任务随执行机恢复"
                    elif execution["state"] == "PAUSE_REQUESTED" and execution.get(
                        "paused_by_machine"
                    ):
                        execution["state"] = "RECONCILIATION_REQUIRED"
                        execution["paused_by_machine"] = False
                        execution["reconciliation_required"] = True
                        execution["message"] = "在途任务必须先完成结果对账"
                    else:
                        continue
                    execution["updated_at"] = utc_now()
                    self._append_result(state, execution)
                    affected.append(copy.deepcopy(execution))
            return copy.deepcopy(state["machine"]), affected, already_resumed

        machine, affected, idempotent = self._store.mutate(mutate)
        return {
            "success": bool(local_sync.get("success")),
            "machine": {
                **machine,
                "idempotent": idempotent,
                "local_service_sync": local_sync,
            },
            "executions": [self._public_execution(item) for item in affected],
        }

    def prepare_transfer(
        self,
        request: dict[str, Any] | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        requested = _request_values(request, values)
        transfer_id = _safe_component(
            _required_text(requested, "transfer_id", "transfer_id"),
            "transfer_id",
        )
        package_type = str(
            requested.get("transfer_type") or requested.get("package_type") or ""
        ).strip()
        if package_type not in TRANSFER_TYPES:
            raise WorkerServiceError("INVALID_PACKAGE_TYPE", "不支持的传输包类型")
        source_name = str(requested.get("source_name") or "").strip()
        if not source_name:
            source_name = str(requested.get("package_name") or "").strip()
        if not source_name or source_name in {".", ".."} or Path(source_name).name != source_name:
            raise WorkerServiceError("INVALID_REQUEST", "source_name只能是文件或目录名称")
        source_stem = Path(source_name).stem
        default_package_name = (
            source_stem if _SAFE_COMPONENT.fullmatch(source_stem) else transfer_id
        )
        package_name = str(
            requested.get("package_name")
            or requested.get("task_id")
            or requested.get("function_id")
            or requested.get("config_name")
            or default_package_name
        ).strip()
        package_name = _safe_component(package_name, "package_name")
        version = str(requested.get("version") or "").strip()
        if version:
            version = _safe_component(version, "version")
        target_machine_id = str(requested.get("target_machine_id") or "").strip()
        allowed = requested.get("allowed_versions")
        manifest = requested.get("manifest")
        if manifest is not None and not isinstance(manifest, dict):
            raise WorkerServiceError("INVALID_MANIFEST", "manifest必须是JSON对象")
        contract = {
            "transfer_id": transfer_id,
            "package_type": package_type,
            "source_name": source_name,
            "package_name": package_name,
            "version": version,
            "target_machine_id": target_machine_id.upper(),
            "allowed_versions": copy.deepcopy(allowed),
            "manifest": copy.deepcopy(manifest),
        }

        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], bool]:
            config = state["config"]
            if (
                target_machine_id
                and target_machine_id.casefold() != str(config["machine_id"]).casefold()
            ):
                raise WorkerServiceError("TARGET_MACHINE_MISMATCH", "传输目标执行机不匹配")
            if allowed:
                versions = _allowed_versions(allowed)
                if config["project_version"] not in versions and "*" not in versions:
                    raise WorkerServiceError(
                        "PROJECT_VERSION_NOT_ALLOWED",
                        "执行机项目版本不在传输允许范围",
                    )
            existing = state["transfers"].get(transfer_id)
            if existing:
                if existing["request_contract"] != contract:
                    raise WorkerServiceError(
                        "TRANSFER_IDEMPOTENCY_CONFLICT",
                        "transfer_id已用于不同传输",
                    )
                return copy.deepcopy(existing), True
            if manifest is not None:
                self._validate_manifest(manifest, contract, config)
            staging_path = self.root / "transfers" / transfer_id / "staging"
            staging_path.mkdir(parents=True, exist_ok=False)
            now = utc_now()
            transfer = {
                **{key: contract[key] for key in contract if key != "manifest"},
                "state": "PREPARED",
                "staging_path": str(staging_path),
                "actual_version": "",
                "installed_path": "",
                "function_id": "",
                "version_status": "",
                "stable_version": "",
                "enabled_scope": self._empty_version_scope(),
                "manifest": copy.deepcopy(manifest),
                "request_contract": contract,
                "error_code": "",
                "message": "传输暂存目录已准备",
                "prepared_at": now,
                "finished_at": None,
            }
            state["transfers"][transfer_id] = transfer
            return copy.deepcopy(transfer), False

        transfer, idempotent = self._store.mutate(mutate)
        return self._transfer_response(transfer, idempotent=idempotent)

    def finalize_transfer(
        self,
        request: dict[str, Any] | str | None = None,
        **values: Any,
    ) -> dict[str, Any]:
        if isinstance(request, str):
            requested = {"transfer_id": request, **values}
        else:
            requested = _request_values(request, values)
        transfer_id = _safe_component(
            _required_text(requested, "transfer_id", "transfer_id"),
            "transfer_id",
        )
        supplied_manifest = requested.get("manifest")
        if supplied_manifest is not None and not isinstance(supplied_manifest, dict):
            raise WorkerServiceError("INVALID_MANIFEST", "manifest必须是JSON对象")

        def mutate(state: dict[str, Any]) -> tuple[dict[str, Any], bool, bool]:
            transfer = state["transfers"].get(transfer_id)
            if transfer is None:
                raise WorkerServiceError("TRANSFER_NOT_FOUND", "传输记录不存在")
            if transfer["state"] == "SUCCESS":
                return copy.deepcopy(transfer), True, True
            if transfer["state"] == "FAILED":
                return copy.deepcopy(transfer), True, False
            try:
                staging = Path(transfer["staging_path"])
                if not staging.is_dir():
                    raise WorkerServiceError("STAGING_NOT_FOUND", "传输暂存目录不存在")
                requested_type = str(
                    requested.get("transfer_type") or requested.get("package_type") or ""
                )
                if requested_type and requested_type != transfer["package_type"]:
                    raise WorkerServiceError("TRANSFER_TYPE_MISMATCH", "传输包类型不匹配")
                allowed = requested.get("allowed_versions")
                if allowed:
                    versions = _allowed_versions(allowed)
                    if state["config"]["project_version"] not in versions and "*" not in versions:
                        raise WorkerServiceError(
                            "PROJECT_VERSION_NOT_ALLOWED",
                            "执行机项目版本不在传输允许范围",
                        )
                uploaded_text = str(requested.get("uploaded_path") or "").strip()
                uploaded = Path(uploaded_text) if uploaded_text else staging
                if not uploaded.exists():
                    raise WorkerServiceError("UPLOADED_PATH_NOT_FOUND", "上传后的文件或目录不存在")
                try:
                    uploaded.resolve().relative_to(staging.resolve())
                except ValueError as exc:
                    raise WorkerServiceError(
                        "UPLOADED_PATH_MISMATCH",
                        "uploaded_path不属于当前暂存目录",
                    ) from exc
                manifest_path = (
                    uploaded / "manifest.json"
                    if uploaded.is_dir()
                    else uploaded
                    if uploaded.name.lower() == "manifest.json"
                    else uploaded.parent / "manifest.json"
                )
                manifest = supplied_manifest
                if manifest is None and manifest_path.is_file():
                    try:
                        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
                    except (OSError, json.JSONDecodeError) as exc:
                        raise WorkerServiceError(
                            "INVALID_MANIFEST",
                            "manifest.json无法读取",
                        ) from exc
                if manifest is None and uploaded.is_file() and uploaded.suffix.lower() == ".json":
                    try:
                        candidate = json.loads(uploaded.read_text(encoding="utf-8-sig"))
                    except (OSError, json.JSONDecodeError):
                        candidate = None
                    if isinstance(candidate, dict) and any(
                        name in candidate
                        for name in (
                            "version",
                            "package_version",
                            "schema_version",
                            "schemaVersion",
                            "package_type",
                            "package_name",
                        )
                    ):
                        manifest = candidate
                if manifest is None:
                    manifest = transfer.get("manifest")
                if transfer["package_type"] == "function_package" and not isinstance(
                    manifest,
                    dict,
                ):
                    raise WorkerServiceError("MANIFEST_REQUIRED", "暂存包缺少manifest.json")
                expected = transfer.get("manifest")
                if expected is not None and expected != manifest:
                    raise WorkerServiceError("MANIFEST_MISMATCH", "实际manifest与准备记录不一致")
                manifest_version = ""
                manifest_identifier = ""
                display_name = ""
                business_module = ""
                if isinstance(manifest, dict):
                    manifest_version = str(
                        manifest.get("version") or manifest.get("package_version") or ""
                    ).strip()
                    display_name = str(manifest.get("package_name") or "").strip()
                    business_module = str(manifest.get("business_module") or "").strip()
                    for name in ("function_id", "task_code", "package_id"):
                        candidate = str(manifest.get(name) or "").strip()
                        if candidate and _SAFE_COMPONENT.fullmatch(candidate):
                            manifest_identifier = candidate
                            break
                actual_version = str(
                    requested.get("version")
                    or manifest_version
                    or transfer.get("version")
                    or transfer_id
                ).strip()
                actual_version = _safe_component(actual_version, "version")
                actual_name = _safe_component(
                    manifest_identifier or transfer["package_name"],
                    "package_name",
                )
                resolved_contract = {
                    **transfer["request_contract"],
                    "version": actual_version,
                    "package_name": actual_name,
                }
                if isinstance(manifest, dict):
                    self._validate_manifest(manifest, resolved_contract, state["config"])
                    if transfer["package_type"] == "function_package":
                        self._validate_function_package_payload(manifest, uploaded)
                destination_record = {
                    **transfer,
                    "version": actual_version,
                    "package_name": actual_name,
                    "business_module": business_module,
                }
                destination = self._transfer_destination(state["config"], destination_record)
                if destination.exists():
                    raise WorkerServiceError(
                        "PACKAGE_VERSION_EXISTS",
                        "目标版本目录已存在，禁止覆盖",
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                bundle = uploaded
                if uploaded.is_file():
                    bundle = staging / "install-bundle"
                    bundle.mkdir()
                    uploaded.replace(bundle / uploaded.name)
                    if isinstance(manifest, dict) and uploaded.name.lower() != "manifest.json":
                        _write_json_atomic(bundle / "manifest.json", manifest)
                self._install_bundle(bundle, destination)
                version_entry: dict[str, Any] = {}
                stable_version = ""
                if transfer["package_type"] == "function_package":
                    version_entry, stable_version = self._register_function_version(
                        state,
                        manifest,
                        destination,
                        actual_version,
                    )
                transfer["state"] = "SUCCESS"
                transfer["actual_version"] = actual_version
                transfer["installed_path"] = str(destination)
                transfer["manifest"] = copy.deepcopy(manifest)
                transfer["display_name"] = display_name or actual_name
                transfer["business_module"] = business_module
                transfer["function_id"] = str(version_entry.get("function_id") or "")
                transfer["version_status"] = str(version_entry.get("status") or "")
                transfer["stable_version"] = stable_version
                transfer["enabled_scope"] = copy.deepcopy(
                    version_entry.get("scope") or self._empty_version_scope()
                )
                transfer["message"] = "传输包已完成安装"
                transfer["finished_at"] = utc_now()
                return copy.deepcopy(transfer), False, True
            except WorkerServiceError as exc:
                transfer["state"] = "FAILED"
                transfer["error_code"] = exc.code
                transfer["message"] = exc.message
                transfer["finished_at"] = utc_now()
                return copy.deepcopy(transfer), False, False
            except OSError:
                transfer["state"] = "FAILED"
                transfer["error_code"] = "TRANSFER_FINALIZE_FAILED"
                transfer["message"] = "传输包安装失败，旧版本保持不变"
                transfer["finished_at"] = utc_now()
                return copy.deepcopy(transfer), False, False

        transfer, idempotent, success = self._store.mutate(mutate)
        return self._transfer_response(
            transfer,
            idempotent=idempotent,
            success=success,
        )

    def collect_results(
        self,
        after_sequence: int = 0,
        *,
        since_sequence: int | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        if since_sequence is not None:
            after_sequence = since_sequence
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int):
            raise WorkerServiceError("INVALID_SEQUENCE", "after_sequence必须是非负整数")
        if after_sequence < 0:
            raise WorkerServiceError("INVALID_SEQUENCE", "after_sequence必须是非负整数")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise WorkerServiceError("INVALID_LIMIT", "limit必须是正整数")
        state = self._store.snapshot()
        candidates = [
            copy.deepcopy(item)
            for item in state["results"]
            if int(item["sequence"]) > after_sequence and item.get("state") in TASK_FINAL_STATES
        ]
        results = candidates[: min(limit, 1000)]
        high_watermark = int(state["next_sequence"]) - 1
        next_sequence = (
            int(results[-1]["sequence"])
            if results
            else max(
                after_sequence,
                high_watermark,
            )
        )
        return {
            "success": True,
            "results": results,
            "next_sequence": next_sequence,
            "high_watermark": high_watermark,
            "has_more": len(candidates) > len(results),
        }

    def _reconcile_execution_status(self, execution: dict[str, Any]) -> dict[str, Any]:
        """用已观察结果或本地任务终态收敛 SSH 中断留下的在途状态。"""

        raw_outcome = execution.get("observed_executor_outcome")
        observed_outcome = isinstance(raw_outcome, dict)
        if not observed_outcome:
            local_task_id = str(execution.get("local_task_id") or "")
            if not local_task_id:
                return execution
            try:
                session = self._local_api_request("GET", "/session")
                token = str(session.get("session_token") or "")
                if not token:
                    raise WorkerServiceError(
                        "LOCAL_SERVICE_UNAVAILABLE",
                        "本地服务没有返回会话令牌",
                    )
                raw_outcome = self._local_api_request(
                    "GET",
                    f"/tasks/{local_task_id}",
                    token=token,
                )
            except WorkerServiceError as exc:
                if execution["state"] != "RUNNING":
                    return execution
                raw_outcome = {
                    "state": "RESULT_UNKNOWN",
                    "error_code": exc.code,
                    "error_classification": "RESULT_UNKNOWN",
                    "manual_action_required": True,
                    "message": "本地任务状态回读中断，需要对账",
                }

        raw_state = self._outcome_state(raw_outcome)
        if raw_state not in LOCAL_TASK_FINAL_STATES and not (
            observed_outcome and raw_state == "BLOCKED"
        ):
            return execution
        outcome = self._normalize_outcome(raw_outcome)

        def mutate(state: dict[str, Any]) -> dict[str, Any]:
            current = self._find_execution(
                state,
                execution["task_id"],
                execution["remote_execution_id"],
            )
            if current["state"] in TASK_FINAL_STATES:
                return copy.deepcopy(current)
            if current["state"] not in {
                "RUNNING",
                "PAUSE_REQUESTED",
                "RECONCILIATION_REQUIRED",
            }:
                return copy.deepcopy(current)
            now = utc_now()
            current.update(
                {
                    "state": outcome["state"],
                    "business_result": outcome["business_result"],
                    "business_result_status": outcome["business_result_status"],
                    "business_result_missing_reason": outcome["business_result_missing_reason"],
                    "item_results": outcome["item_results"],
                    "item_results_status": outcome["item_results_status"],
                    "item_results_missing_reason": outcome["item_results_missing_reason"],
                    "evidence_index": outcome["evidence_index"],
                    "incremental_data": outcome["incremental_data"],
                    "incremental_data_status": outcome["incremental_data_status"],
                    "incremental_data_missing_reason": outcome["incremental_data_missing_reason"],
                    "error_code": outcome["error_code"],
                    "error_classification": outcome["error_classification"],
                    "manual_action_required": outcome["manual_action_required"],
                    "recommended_action": outcome["recommended_action"],
                    "risk_level": outcome["risk_level"],
                    "message": outcome["message"],
                    "raw_result": outcome["raw_result"],
                    "reconciliation_required": outcome["state"] == "RESULT_UNKNOWN",
                    "finished_at": now,
                    "updated_at": now,
                }
            )
            self._append_result(state, current)
            return copy.deepcopy(current)

        return self._store.mutate(mutate)

    def _execute_via_local_api(self, payload: dict[str, Any]) -> dict[str, Any]:
        """通过常驻本地服务执行，避免在 SSH 请求中重建 ApplicationService。"""

        task_body = {
            key: copy.deepcopy(payload[key])
            for key in (
                "source",
                "account_slot",
                "capability",
                "request_summary",
                "operation",
                "parameters",
                "source_type",
                "source_event_id",
                "parent_task_id",
                "target_type",
                "target_id",
                "platform",
            )
            if key in payload
        }
        task_body.setdefault("source", "manager")
        try:
            session = self._local_api_request("GET", "/session")
            token = str(session.get("session_token") or "")
            if not token:
                raise WorkerServiceError(
                    "LOCAL_SERVICE_UNAVAILABLE",
                    "本地服务没有返回会话令牌",
                )
            created = self._local_api_request("POST", "/tasks", task_body, token=token)
            local_task = created.get("task") or {}
            local_task_id = str(local_task.get("task_id") or "")
            if not local_task_id:
                raise WorkerServiceError(
                    "LOCAL_SERVICE_UNAVAILABLE",
                    "本地服务没有返回任务编号",
                )
            self._record_local_task_id(
                str(payload.get("remote_execution_id") or ""),
                local_task_id,
            )
            if local_task.get("state") != "QUEUED":
                return {
                    "state": "BLOCKED",
                    "error_code": "LOCAL_CONFIRMATION_REQUIRED",
                    "message": "本地规则要求先完成确认",
                    "local_task_id": local_task_id,
                }
            try:
                executed = self._local_api_request(
                    "POST",
                    f"/tasks/{local_task_id}/execute",
                    {},
                    token=token,
                )
            except WorkerServiceError as exc:
                return {
                    "state": "RESULT_UNKNOWN",
                    "error_code": exc.code,
                    "error_classification": "RESULT_UNKNOWN",
                    "manual_action_required": True,
                    "message": "本地任务已开始，但执行回读中断，需要对账",
                    "local_task_id": local_task_id,
                }
            return {**executed, "local_task_id": local_task_id}
        except WorkerServiceError as exc:
            return {
                "state": "BLOCKED",
                "error_code": exc.code,
                "message": exc.message,
            }
        except (HTTPError, URLError, OSError, TimeoutError, ValueError):
            return {
                "state": "BLOCKED",
                "error_code": "LOCAL_SERVICE_UNAVAILABLE",
                "message": "本地应用服务不可用",
            }

    def _record_local_task_id(self, remote_execution_id: str, local_task_id: str) -> None:
        if not remote_execution_id:
            return

        def mutate(state: dict[str, Any]) -> None:
            execution = state["executions"].get(remote_execution_id)
            if execution is not None:
                execution["local_task_id"] = local_task_id
                execution["updated_at"] = utc_now()

        self._store.mutate(mutate)

    def _sync_local_control(
        self,
        action: str,
        local_task_id: str | None = None,
    ) -> dict[str, Any]:
        if self._local_control is not None:
            try:
                result = self._local_control(action, local_task_id)
                if isinstance(result, dict):
                    return {"success": result.get("success") is not False, **result}
            except Exception as exc:
                return {
                    "success": False,
                    "error": {"code": "LOCAL_CONTROL_FAILED", "message": str(exc)},
                }
            return {
                "success": False,
                "error": {"code": "LOCAL_CONTROL_FAILED", "message": "本地控制返回格式不正确"},
            }
        try:
            session = self._local_api_request("GET", "/session")
            token = str(session.get("session_token") or "")
            if not token:
                raise WorkerServiceError(
                    "LOCAL_SERVICE_UNAVAILABLE",
                    "本地服务没有返回会话令牌",
                )
            paths = {
                "pause-machine": "/system/pause",
                "resume-machine": "/system/resume",
            }
            path = paths.get(action)
            if action == "cancel-task" and local_task_id:
                path = f"/tasks/{local_task_id}/cancel"
            if not path:
                raise WorkerServiceError("INVALID_LOCAL_CONTROL", "未知本地控制动作")
            result = self._local_api_request("POST", path, {}, token=token)
            return {"success": True, "response": result}
        except WorkerServiceError as exc:
            return {"success": False, "error": {"code": exc.code, "message": exc.message}}

    def _local_api_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        token: str = "",
    ) -> dict[str, Any]:
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-XHS-Session"] = token
            headers["X-Auto-XHS-Session"] = token
        request = Request(
            f"{self._local_api_url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            timeout = float(os.getenv("XHS_LOCAL_API_TIMEOUT", "10"))
        except ValueError:
            timeout = 10.0
        try:
            with urlopen(request, timeout=timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8")).get("error") or {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                error = {}
            raise WorkerServiceError(
                str(error.get("code") or "LOCAL_SERVICE_REJECTED"),
                str(error.get("message") or "本地应用服务拒绝请求"),
            ) from exc
        except (URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise WorkerServiceError(
                "LOCAL_SERVICE_UNAVAILABLE",
                "本地应用服务不可用",
            ) from exc
        if not isinstance(value, dict):
            raise WorkerServiceError("LOCAL_SERVICE_UNAVAILABLE", "本地服务返回格式不正确")
        return value

    @staticmethod
    def _normalize_outcome(value: dict[str, Any]) -> dict[str, Any]:
        task = value.get("task") if isinstance(value.get("task"), dict) else {}
        nested = value.get("result") if isinstance(value.get("result"), dict) else {}

        business_result_present = "business_result" in value or "result" in value
        raw_business_result = (
            value.get("business_result") if "business_result" in value else value.get("result")
        )
        if business_result_present and isinstance(raw_business_result, (dict, list)):
            business_result = copy.deepcopy(raw_business_result)
            business_result_status = str(
                value.get("business_result_status")
                or nested.get("business_result_status")
                or "observed"
            )
            business_result_missing_reason = str(
                value.get("business_result_missing_reason")
                or nested.get("business_result_missing_reason")
                or ""
            )
        elif business_result_present:
            business_result = {}
            business_result_status = "failed"
            business_result_missing_reason = "业务结果格式不是对象或数组"
        else:
            business_result = {}
            business_result_status = "missing"
            business_result_missing_reason = "执行结果未提供业务结果"

        def first_present(
            mappings: tuple[dict[str, Any], ...],
            names: tuple[str, ...],
        ) -> tuple[Any, bool]:
            for mapping in mappings:
                for name in names:
                    if name in mapping:
                        return mapping[name], True
            return None, False

        raw_state = str(value.get("state") or value.get("status") or task.get("state") or "")
        state_map = {
            "SUCCESS": "SUCCESS",
            "PARTIAL_SUCCESS": "PARTIAL_SUCCESS",
            "FAILED": "FAILED",
            "CANCELLED": "CANCELLED",
            "BLOCKED": "BLOCKED",
            "WAITING_APPROVAL": "BLOCKED",
            "RESULT_UNKNOWN": "RESULT_UNKNOWN",
        }
        state = state_map.get(
            raw_state.upper(), "SUCCESS" if value.get("success", True) else "FAILED"
        )
        item_results, item_results_present = first_present(
            (value, nested),
            ("item_results", "items", "recipient_results", "results", "feeds"),
        )
        if item_results_present and isinstance(item_results, list):
            item_results_status = str(
                value.get("item_results_status") or nested.get("item_results_status") or "observed"
            )
            item_results_missing_reason = str(
                value.get("item_results_missing_reason")
                or nested.get("item_results_missing_reason")
                or ""
            )
        elif item_results_present:
            item_results = []
            item_results_status = "failed"
            item_results_missing_reason = "逐项结果格式不是数组"
        else:
            item_results = []
            item_results_status = "missing"
            item_results_missing_reason = "执行结果未提供逐项结果"
        evidence_index = value.get("evidence_index", nested.get("evidence_index", {}))
        if not isinstance(evidence_index, (dict, list)):
            evidence_index = {}
        incremental_data, incremental_data_present = first_present(
            (value, nested),
            ("incremental_data", "snapshots", "inbound_events"),
        )
        if incremental_data_present and isinstance(incremental_data, list):
            normalized_incremental_data = incremental_data
            incremental_data_status = str(
                value.get("incremental_data_status")
                or nested.get("incremental_data_status")
                or "observed"
            )
            incremental_data_missing_reason = str(
                value.get("incremental_data_missing_reason")
                or nested.get("incremental_data_missing_reason")
                or ""
            )
        elif incremental_data_present and isinstance(incremental_data, dict):
            normalized_incremental_data = []
            for name in (
                "events",
                "inbound_events",
                "snapshots",
                "reconciled_outbound",
            ):
                items = incremental_data.get(name)
                if isinstance(items, list):
                    normalized_incremental_data.extend(items)
            if not normalized_incremental_data and incremental_data:
                normalized_incremental_data = [incremental_data]
            incremental_data_status = "observed"
            incremental_data_missing_reason = ""
        elif incremental_data_present:
            normalized_incremental_data = []
            incremental_data_status = "failed"
            incremental_data_missing_reason = "增量数据格式不是对象或数组"
        else:
            normalized_incremental_data = []
            incremental_data_status = "missing"
            incremental_data_missing_reason = "执行结果未提供增量数据"
        error = value.get("error") if isinstance(value.get("error"), dict) else {}
        error_code = str(
            value.get("error_code") or task.get("error_code") or error.get("code") or ""
        )
        error_classification = str(value.get("error_classification") or "")
        if not error_classification and state != "SUCCESS":
            error_classification = {
                "PARTIAL_SUCCESS": "PARTIAL_SUCCESS",
                "CANCELLED": "CANCELLED",
                "BLOCKED": "BLOCKED",
                "RESULT_UNKNOWN": "RESULT_UNKNOWN",
                "FAILED": "EXECUTION_FAILED",
            }[state]
        manual_action = value.get("manual_action_required")
        if not isinstance(manual_action, bool):
            manual_action = state in {"FAILED", "BLOCKED", "RESULT_UNKNOWN"}
        recommended_action = str(
            value.get("recommended_action")
            or task.get("recommended_action")
            or nested.get("recommended_action")
            or (
                "先读取本地任务和平台当前状态，再决定是否继续"
                if state == "RESULT_UNKNOWN"
                else "检查执行机身份、版本、配额和任务参数"
                if state in {"FAILED", "BLOCKED"}
                else ""
            )
        )
        message = str(
            value.get("message")
            or task.get("result_summary")
            or error.get("message")
            or ("任务执行完成" if state == "SUCCESS" else "任务执行未完成")
        )
        return {
            "state": state,
            "business_result": business_result,
            "business_result_status": business_result_status,
            "business_result_missing_reason": business_result_missing_reason,
            "item_results": copy.deepcopy(item_results),
            "item_results_status": item_results_status,
            "item_results_missing_reason": item_results_missing_reason,
            "evidence_index": copy.deepcopy(evidence_index),
            "incremental_data": copy.deepcopy(normalized_incremental_data),
            "incremental_data_status": incremental_data_status,
            "incremental_data_missing_reason": incremental_data_missing_reason,
            "error_code": error_code,
            "error_classification": error_classification,
            "manual_action_required": manual_action,
            "recommended_action": recommended_action,
            "risk_level": str(task.get("risk_level") or value.get("risk_level") or ""),
            "message": message,
            "raw_result": copy.deepcopy(value),
        }

    @staticmethod
    def _outcome_state(value: dict[str, Any]) -> str:
        task = value.get("task") if isinstance(value.get("task"), dict) else {}
        return str(value.get("state") or value.get("status") or task.get("state") or "").upper()

    @staticmethod
    def _find_execution(
        state: dict[str, Any],
        task_id: str | None,
        remote_execution_id: str | None,
    ) -> dict[str, Any]:
        execution_id = str(remote_execution_id or "").strip()
        if not execution_id and task_id:
            execution_id = str(state["task_index"].get(task_id) or "")
        execution = state["executions"].get(execution_id)
        if execution is None:
            raise WorkerServiceError("TASK_NOT_FOUND", "执行机任务不存在")
        return execution

    @staticmethod
    def _append_result(state: dict[str, Any], execution: dict[str, Any]) -> None:
        if execution.get("state") not in TASK_FINAL_STATES:
            return
        sequence = int(state["next_sequence"])
        state["next_sequence"] = sequence + 1
        state["results"].append(
            {
                "sequence": sequence,
                "result_revision": sequence,
                "remote_execution_id": execution["remote_execution_id"],
                "task_id": execution["task_id"],
                "dispatch_id": execution["dispatch_id"],
                "project_version": execution["project_version"],
                "function_id": execution.get("function_id", ""),
                "actual_version": execution.get("actual_version") or execution["project_version"],
                "version_status": execution.get("version_status", "PROJECT"),
                "state": execution["state"],
                "business_result": copy.deepcopy(execution.get("business_result", {})),
                "business_result_status": execution.get("business_result_status", "missing"),
                "business_result_missing_reason": execution.get(
                    "business_result_missing_reason", ""
                ),
                "item_results": copy.deepcopy(execution.get("item_results") or []),
                "item_results_status": execution.get("item_results_status", "missing"),
                "item_results_missing_reason": execution.get("item_results_missing_reason", ""),
                "evidence_index": copy.deepcopy(execution.get("evidence_index") or {}),
                "incremental_data": copy.deepcopy(execution.get("incremental_data") or []),
                "incremental_data_status": execution.get("incremental_data_status", "missing"),
                "incremental_data_missing_reason": execution.get(
                    "incremental_data_missing_reason", ""
                ),
                "error_code": execution.get("error_code", ""),
                "error_classification": execution.get("error_classification", ""),
                "manual_action_required": bool(execution.get("manual_action_required", False)),
                "recommended_action": execution.get("recommended_action", ""),
                "risk_level": execution.get("risk_level", ""),
                "message": execution.get("message", ""),
                "result_summary": execution.get("message", ""),
                "recorded_at": utc_now(),
            }
        )

    @staticmethod
    def _public_execution(execution: dict[str, Any]) -> dict[str, Any]:
        value = copy.deepcopy(execution)
        value.pop("accepted_request", None)
        value.pop("paused_by_machine", None)
        value.setdefault("result_summary", value.get("message", ""))
        return value

    def _execution_response(
        self,
        execution: dict[str, Any],
        *,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        value = self._public_execution(execution)
        value["idempotent"] = idempotent
        return {"success": True, "execution": value}

    @staticmethod
    def _transfer_response(
        transfer: dict[str, Any],
        *,
        idempotent: bool = False,
        success: bool = True,
    ) -> dict[str, Any]:
        value = copy.deepcopy(transfer)
        value.pop("request_contract", None)
        value["idempotent"] = idempotent
        return {"success": success, "transfer": value}

    @staticmethod
    def _empty_version_scope() -> dict[str, list[str]]:
        return {
            "manager_ids": [],
            "machine_ids": [],
            "account_slots": [],
            "allowed_actions": [],
        }

    @classmethod
    def _normalize_version_scope(
        cls,
        value: dict[str, Any] | None,
        *,
        allow_empty: bool = False,
    ) -> dict[str, list[str]]:
        if not isinstance(value, dict):
            raise WorkerServiceError(
                "INVALID_VERSION_SCOPE",
                "启用版本必须明确经理、设备和账号范围",
            )
        aliases = {
            "manager_ids": ("manager_ids", "managers"),
            "machine_ids": ("machine_ids", "devices"),
            "account_slots": ("account_slots", "accounts"),
            "allowed_actions": ("allowed_actions", "actions"),
        }
        scope: dict[str, list[str]] = {}
        for target, names in aliases.items():
            raw = next((value[name] for name in names if name in value), None)
            if target == "allowed_actions" and raw is None:
                raw = ["*"] if not allow_empty else []
            if not isinstance(raw, list):
                raise WorkerServiceError("INVALID_VERSION_SCOPE", f"scope.{target}必须是数组")
            normalized = sorted({str(item).strip() for item in raw if str(item).strip()})
            if not normalized and not allow_empty:
                raise WorkerServiceError("INVALID_VERSION_SCOPE", f"scope.{target}不能为空")
            scope[target] = normalized
        return scope

    @staticmethod
    def _version_sort_key(version: str) -> tuple[str, ...]:
        return tuple(
            f"0{int(part):020d}" if part.isdigit() else f"1{part.casefold()}"
            for part in re.split(r"([0-9]+)", str(version))
            if part
        )

    @classmethod
    def _scope_allows(
        cls,
        entry: dict[str, Any],
        *,
        manager_id: str,
        machine_id: str,
        account_slot: str,
        capability: str,
    ) -> bool:
        scope = entry.get("scope") if isinstance(entry.get("scope"), dict) else {}

        def includes(name: str, value: str) -> bool:
            allowed = scope.get(name)
            return isinstance(allowed, list) and ("*" in allowed or value in allowed)

        return (
            includes("manager_ids", manager_id)
            and includes("machine_ids", machine_id)
            and includes("account_slots", account_slot)
            and includes("allowed_actions", capability)
        )

    @classmethod
    def _select_function_version(
        cls,
        state: dict[str, Any],
        task: dict[str, Any],
        config: dict[str, Any],
    ) -> dict[str, str]:
        function_id = str(task.get("function_id") or "").strip()
        if not function_id:
            return {
                "function_id": "",
                "actual_version": str(config["project_version"]),
                "version_status": "PROJECT",
            }
        function_id = _safe_component(function_id, "function_id")
        manager_id = str(task.get("manager_id") or "").strip()
        account_slot = str(task.get("account_slot") or "").strip()
        capability = str(task.get("capability") or "").strip()
        if not manager_id:
            raise WorkerServiceError("INVALID_REQUEST", "功能包任务缺少manager_id")
        allowed = task.get("allowed_function_versions")
        if not isinstance(allowed, list) or not all(str(item).strip() for item in allowed):
            raise WorkerServiceError(
                "INVALID_REQUEST",
                "功能包任务必须给出allowed_function_versions",
            )
        allowed_versions = [str(item).strip() for item in allowed]
        registry = state["function_versions"].get(function_id)
        if not isinstance(registry, dict):
            raise WorkerServiceError("FUNCTION_NOT_INSTALLED", "执行机未安装任务所需功能包")
        entries = registry.get("versions") if isinstance(registry.get("versions"), dict) else {}
        candidates: list[str] = []
        if "*" in allowed_versions:
            trial_versions = [
                version
                for version, entry in entries.items()
                if isinstance(entry, dict) and entry.get("status") == "TRIAL"
            ]
            trial_versions.sort(key=cls._version_sort_key, reverse=True)
            candidates.extend(trial_versions)
            stable = str(registry.get("stable_version") or "")
            if stable:
                candidates.append(stable)
        else:
            candidates.extend(allowed_versions)
        seen: set[str] = set()
        for version in candidates:
            if version in seen:
                continue
            seen.add(version)
            entry = entries.get(version)
            if not isinstance(entry, dict) or entry.get("status") not in {"FORMAL", "TRIAL"}:
                continue
            compatible = entry.get("compatible_project_versions") or ["*"]
            if config["project_version"] not in compatible and "*" not in compatible:
                continue
            if not cls._scope_allows(
                entry,
                manager_id=manager_id,
                machine_id=config["machine_id"],
                account_slot=account_slot,
                capability=capability,
            ):
                continue
            return {
                "function_id": function_id,
                "actual_version": version,
                "version_status": str(entry["status"]),
            }
        raise WorkerServiceError(
            "FUNCTION_VERSION_NOT_ALLOWED",
            "没有符合项目兼容性和启用范围的功能包版本",
        )

    @classmethod
    def _register_function_version(
        cls,
        state: dict[str, Any],
        manifest: dict[str, Any],
        destination: Path,
        version: str,
    ) -> tuple[dict[str, Any], str]:
        function_id = str(manifest["function_id"]).strip()
        registry = state["function_versions"].setdefault(
            function_id,
            {
                "function_id": function_id,
                "business_module": str(manifest["business_module"]).strip(),
                "function_name": str(manifest["function_name"]).strip(),
                "stable_version": "",
                "versions": {},
                "created_at": utc_now(),
                "updated_at": utc_now(),
            },
        )
        compatible = manifest.get("compatible_project_versions")
        if compatible is None:
            compatible = manifest.get("allowed_versions")
        if not isinstance(compatible, list) or not compatible:
            compatible = ["*"]
        entry = {
            "function_id": function_id,
            "version": version,
            "status": "DISABLED",
            "scope": cls._empty_version_scope(),
            "installed_path": str(destination),
            "entrypoint": str(manifest["entrypoint"]),
            "rollback_version": str(manifest.get("rollback_version") or ""),
            "compatible_project_versions": [str(item) for item in compatible],
            "installed_at": utc_now(),
            "policy_updated_at": None,
        }
        registry["versions"][version] = entry
        registry["updated_at"] = utc_now()
        return copy.deepcopy(entry), str(registry.get("stable_version") or "")

    @staticmethod
    def _transfer_destination(
        config: dict[str, Any],
        transfer: dict[str, Any],
    ) -> Path:
        if transfer["package_type"] == "task_package":
            root = Path(config["task_inbox_root"])
        elif transfer["package_type"] == "function_package" and transfer.get("business_module"):
            module_name = str(transfer["business_module"]).replace("\\", "/").split("/")[-1]
            module_name = "".join(
                character if character.isalnum() or character in {"-", "_", "."} else "_"
                for character in module_name
            ).strip(".")
            if not module_name:
                raise WorkerServiceError("INVALID_MANIFEST", "功能包业务模块无效")
            root = Path(config["package_root"]) / module_name
        else:
            root = Path(config["package_root"]) / transfer["package_type"]
        return root / transfer["package_name"] / transfer["version"]

    @staticmethod
    def _install_bundle(bundle: Path, destination: Path) -> None:
        """优先原子移动；跨磁盘时在目标盘完整复制后再同盘改名。"""

        try:
            bundle.replace(destination)
            return
        except OSError:
            pass
        temporary = destination.parent / f".{destination.name}.{uuid.uuid4().hex}.installing"
        try:
            if bundle.is_dir():
                shutil.copytree(bundle, temporary)
            else:
                temporary.mkdir(parents=True)
                shutil.copy2(bundle, temporary / bundle.name)
            temporary.rename(destination)
        except OSError:
            if temporary.is_dir():
                shutil.rmtree(temporary, ignore_errors=True)
            elif temporary.exists():
                temporary.unlink(missing_ok=True)
            raise
        if bundle.is_dir():
            shutil.rmtree(bundle, ignore_errors=True)
        else:
            bundle.unlink(missing_ok=True)

    @staticmethod
    def _validate_manifest(
        manifest: dict[str, Any],
        contract: dict[str, Any],
        config: dict[str, Any],
    ) -> None:
        schema = manifest.get("schema_version", manifest.get("schemaVersion"))
        if schema is not None and str(schema) not in SUPPORTED_MANIFEST_VERSIONS:
            raise WorkerServiceError("MANIFEST_VERSION_UNSUPPORTED", "manifest版本不兼容")
        package_type = str(manifest.get("package_type") or "")
        if package_type and package_type != contract["package_type"]:
            raise WorkerServiceError("MANIFEST_MISMATCH", "manifest包类型不匹配")
        actual_version = str(manifest.get("version") or manifest.get("package_version") or "")
        if not actual_version:
            raise WorkerServiceError("INVALID_MANIFEST", "manifest缺少version")
        if contract.get("version") and actual_version != contract["version"]:
            raise WorkerServiceError("MANIFEST_MISMATCH", "manifest版本与传输版本不匹配")
        target = str(manifest.get("target_machine_id") or "")
        if target and target.casefold() != str(config["machine_id"]).casefold():
            raise WorkerServiceError("TARGET_MACHINE_MISMATCH", "manifest目标执行机不匹配")
        versions = manifest.get("allowed_versions")
        if versions is None:
            versions = manifest.get("compatible_project_versions")
        if versions is not None:
            allowed = _allowed_versions(versions)
            if config["project_version"] not in allowed and "*" not in allowed:
                raise WorkerServiceError(
                    "MANIFEST_PROJECT_VERSION_MISMATCH",
                    "manifest不兼容当前项目版本",
                )
        required = str(manifest.get("project_version") or "")
        if required and required != config["project_version"]:
            raise WorkerServiceError(
                "MANIFEST_PROJECT_VERSION_MISMATCH",
                "manifest要求的项目版本不匹配",
            )

    @staticmethod
    def _validate_function_package_payload(
        manifest: dict[str, Any],
        uploaded: Path,
    ) -> None:
        if not uploaded.is_dir():
            raise WorkerServiceError("INVALID_FUNCTION_PACKAGE", "功能包必须上传为目录")
        required_manifest_fields = (
            "business_module",
            "function_id",
            "task_code",
            "function_name",
            "package_name",
            "version",
            "applicable_layer",
            "execution_mode",
            "supported_environment",
            "dependencies",
            "call_relationships",
            "entrypoint",
            "rollback_version",
        )
        missing_fields = [name for name in required_manifest_fields if name not in manifest]
        if missing_fields:
            raise WorkerServiceError(
                "INVALID_MANIFEST",
                f"正式功能包manifest缺少字段: {', '.join(missing_fields)}",
            )
        required_text_fields = (
            "business_module",
            "function_id",
            "task_code",
            "function_name",
            "package_name",
            "version",
            "applicable_layer",
            "execution_mode",
            "entrypoint",
        )
        empty_fields = [name for name in required_text_fields if not str(manifest[name]).strip()]
        if empty_fields:
            raise WorkerServiceError(
                "INVALID_MANIFEST",
                f"正式功能包manifest字段不能为空: {', '.join(empty_fields)}",
            )
        if not isinstance(manifest.get("dependencies"), list) or not manifest["dependencies"]:
            raise WorkerServiceError("INVALID_MANIFEST", "功能包dependencies必须是非空数组")
        if (
            not isinstance(manifest.get("call_relationships"), list)
            or not manifest["call_relationships"]
        ):
            raise WorkerServiceError("INVALID_MANIFEST", "功能包call_relationships必须是非空数组")
        supported_environment = manifest.get("supported_environment")
        if not (
            (isinstance(supported_environment, str) and supported_environment.strip())
            or (
                isinstance(supported_environment, list)
                and any(str(item).strip() for item in supported_environment)
            )
        ):
            raise WorkerServiceError("INVALID_MANIFEST", "功能包supported_environment不能为空")
        identifier = next(
            (
                str(manifest.get(name) or "").strip()
                for name in ("function_id", "task_code", "package_id")
                if str(manifest.get(name) or "").strip()
            ),
            "",
        )
        if not identifier or not _SAFE_COMPONENT.fullmatch(identifier):
            raise WorkerServiceError("INVALID_MANIFEST", "功能包manifest缺少有效功能编号")
        entrypoint = str(manifest.get("entrypoint") or "").strip()
        if not entrypoint:
            raise WorkerServiceError("INVALID_MANIFEST", "功能包manifest缺少运行入口")
        entrypoint_path = Path(entrypoint)
        if entrypoint_path.is_absolute():
            raise WorkerServiceError("INVALID_MANIFEST", "功能包运行入口必须是包内相对路径")
        resolved_entrypoint = (uploaded / entrypoint_path).resolve()
        try:
            resolved_entrypoint.relative_to(uploaded.resolve())
        except ValueError as exc:
            raise WorkerServiceError("INVALID_MANIFEST", "功能包运行入口超出包目录") from exc
        if not resolved_entrypoint.is_file():
            raise WorkerServiceError("INVALID_FUNCTION_PACKAGE", "功能包运行入口文件不存在")
        required_directories = ("config", "runtime", "tests")
        required_files = (
            "README.md",
            "CHANGELOG.md",
            "ROLLBACK.md",
            "RUN_RESULT.json",
            "VERIFICATION_REPORT.md",
        )
        missing_paths = [name for name in required_directories if not (uploaded / name).is_dir()]
        missing_paths.extend(name for name in required_files if not (uploaded / name).is_file())
        if missing_paths:
            raise WorkerServiceError(
                "INVALID_FUNCTION_PACKAGE",
                f"正式功能包缺少文件或目录: {', '.join(missing_paths)}",
            )
        history_replays = [
            path for path in (uploaded / "tests").glob("history_replay.*") if path.is_file()
        ]
        if not history_replays:
            raise WorkerServiceError("INVALID_FUNCTION_PACKAGE", "功能包缺少历史回放样本")
