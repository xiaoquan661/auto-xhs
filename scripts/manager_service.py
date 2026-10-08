"""Manager-side orchestration for Windows executors over a fixed SSH entrypoint."""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import uuid
from collections import Counter
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from daily_operations_plan import generate_account, material
from daily_operations_plan import plan_date as parse_plan_date
from employee_operations import PLAN_METRICS, EmployeeOperations, normalize_config
from manager_memory import ManagerMemory
from manager_polling import ManagerPolling
from manager_store import ManagerStore
from manager_transport import ManagerTransportError, OpenSSHTransport
from operations_policy import PUBLISH_ACTIONS, check_operations_policy
from service_errors import ServiceError

EXPECTED_EXECUTOR_ROLE = "executor"
OUTBOX_COLLECTION_BATCH_SIZE = 20
REMOTE_TERMINAL_STATES = {
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "FAILED",
    "BLOCKED",
    "CANCELLED",
    "RESULT_UNKNOWN",
}
ACCOUNT_HEALTH_STATES = {"READY", "WARNING", "BLOCKED", "UNKNOWN"}
ACCOUNT_BLOCKED_STATES = {
    "BLOCKED",
    "LOGIN_REQUIRED",
    "IDENTITY_MISMATCH",
    "PLATFORM_MISMATCH",
}
ACCOUNT_WARNING_STATES = {
    "IDENTITY_REQUIRED",
    "IDENTITY_CHECK_REQUIRED",
    "SWITCH_PENDING",
    "WAITING_EXTENSION",
}
PERSONA_STATES = {"ACTIVE", "INACTIVE"}
GOAL_STATES = {"DRAFT", "ACTIVE", "PAUSED", "COMPLETED"}
OPERATION_TASK_STATES = {
    "WAITING_AGENT",
    "PLANNING",
    "DISPATCHING",
    "RUNNING",
    "RECONCILIATION_REQUIRED",
    "COMPLETED",
    "BLOCKED",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ManagerService(ManagerMemory, ManagerPolling):
    """Persist remote intent separately from transport and platform outcomes."""

    def debug_action(self, action: str, payload: dict | None = None) -> dict:
        from manager_debug import ManagerDebugService

        return ManagerDebugService(self).action(action, payload or {})

    def __init__(
        self,
        *,
        store: ManagerStore | None = None,
        transport: OpenSSHTransport | None = None,
        machine_stale_seconds: float | None = None,
        account_stale_seconds: float | None = None,
    ) -> None:
        self.store = store or ManagerStore()
        self.transport = transport or OpenSSHTransport()
        if isinstance(self.transport, OpenSSHTransport):
            self.transport.activity_log = self.store.root / "logs" / "manager-sync.log"
        self._machine_stale_seconds = machine_stale_seconds
        self._account_stale_seconds = account_stale_seconds
        self._init_polling()

    @property
    def machine_stale_seconds(self) -> float:
        if self._machine_stale_seconds is not None:
            return float(self._machine_stale_seconds)
        return float(self.polling_settings["health_accounts_seconds"] * 3)

    @property
    def account_stale_seconds(self) -> float:
        if self._account_stale_seconds is not None:
            return float(self._account_stale_seconds)
        return float(self.polling_settings["health_accounts_seconds"] * 3)

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
            "last_account_sync_at": None,
            "last_account_check_at": None,
            "account_sync_status": "NEVER",
            "account_sync_error_code": "",
            "account_sync_error": "",
            "last_result_sync_at": None,
            "last_result_success_at": None,
            "result_sync_status": "NEVER",
            "result_sync_error_code": "",
            "result_sync_error": "",
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
        machines = [self._with_machine_freshness(item) for item in self.store.list("machines")]
        machines.sort(key=lambda item: item["machine_id"])
        return {"success": True, "machines": machines}

    def list_accounts(self, machine_id: str | None = None) -> dict:
        """Return manager-cached account snapshots without contacting executors."""

        normalized_machine_id = ""
        if machine_id:
            normalized_machine_id = self._get_machine(machine_id)["machine_id"]
        accounts = self.store.list("accounts")
        if normalized_machine_id:
            accounts = [
                item for item in accounts if item.get("machine_id") == normalized_machine_id
            ]
        accounts = [
            self._with_account_task(self._with_account_freshness(item)) for item in accounts
        ]
        accounts.sort(key=lambda item: (item["machine_id"], item["account_slot"].casefold()))
        counts = Counter(str(item.get("health_state") or "UNKNOWN") for item in accounts)
        counts["RUNNING"] = sum(item.get("agent_task_status") == "RUNNING" for item in accounts)
        return {
            "success": True,
            "accounts": accounts,
            "summary": {
                "total": len(accounts),
                **{state: int(counts[state]) for state in sorted(ACCOUNT_HEALTH_STATES)},
                "RUNNING": int(counts["RUNNING"]),
            },
        }

    def update_account_management(
        self,
        machine_id: str,
        account: str,
        request: dict,
    ) -> dict:
        """Update manager-owned account metadata without changing worker observations."""

        machine = self._get_machine(machine_id)
        account_slot = str(account or "").strip()
        key = self._account_key(machine["machine_id"], account_slot)
        existing = self.store.get("accounts", key)
        if existing is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "账号尚未进入经理机账号目录", 404)
        note = str(request.get("account_note") or "").strip()
        if len(note) > 500:
            raise ServiceError("INVALID_ACCOUNT_NOTE", "账号备注不能超过500个字符")
        scheduling_enabled = request.get(
            "scheduling_enabled",
            existing.get("scheduling_enabled", True),
        )
        if not isinstance(scheduling_enabled, bool):
            raise ServiceError("INVALID_SCHEDULING_STATE", "调度启用状态必须是布尔值")
        management_updated_at = utc_now()
        updated = {
            **existing,
            "account_note": note,
            "scheduling_enabled": scheduling_enabled,
            "management_source_label": "经理机配置",
            "management_updated_at": management_updated_at,
            "management_sync_status": "PENDING",
            "management_sync_message": "",
        }
        stored = self.store.put("accounts", key, updated)
        sync_status = "SUCCESS"
        sync_message = "账号管理信息已同步到员工机"
        try:
            response = self.transport.invoke(
                machine,
                "account-management",
                {
                    "account": account_slot,
                    "account_note": note,
                    "scheduling_enabled": scheduling_enabled,
                    "management_updated_at": management_updated_at,
                },
                timeout_seconds=30,
            )
            remote_error = self._remote_error(response)
            if remote_error:
                sync_status = "UNKNOWN"
                sync_message = remote_error[1]
        except ManagerTransportError as exc:
            sync_status = "UNKNOWN"
            sync_message = str(exc)
        stored = self.store.update(
            "accounts",
            key,
            lambda current: {
                **current,
                "management_sync_status": sync_status,
                "management_sync_message": sync_message,
            },
        )
        return {
            "success": True,
            "status": sync_status,
            "message": sync_message,
            "account": self._with_account_task(self._with_account_freshness(stored)),
        }

    def list_account_alerts(self, machine_id: str | None = None) -> dict:
        normalized_machine_id = ""
        if machine_id:
            normalized_machine_id = self._get_machine(machine_id)["machine_id"]
        alerts_by_key = {
            str(item.get("account_key") or ""): item
            for item in self.store.list("account_alerts")
            if item.get("active")
        }
        for account in self.store.list("accounts"):
            account = self._with_account_freshness(account)
            if account.get("data_freshness") != "STALE":
                continue
            key = str(account.get("account_key") or "")
            alerts_by_key[key] = {
                "alert_id": f"account-alert:{key}",
                "account_key": key,
                "machine_id": account.get("machine_id"),
                "account_slot": account.get("account_slot"),
                "severity": "UNKNOWN",
                "reason": account.get("stale_reason"),
                "recommended_action": account.get("recommended_action"),
                "active": True,
                "first_seen_at": account.get("updated_at"),
                "last_seen_at": account.get("updated_at"),
                "resolved_at": None,
                "transient": True,
            }
        alerts = list(alerts_by_key.values())
        if normalized_machine_id:
            alerts = [item for item in alerts if item.get("machine_id") == normalized_machine_id]
        alerts.sort(
            key=lambda item: (item.get("last_seen_at") or "", item.get("account_key") or ""),
            reverse=True,
        )
        return {"success": True, "alerts": alerts, "total": len(alerts)}

    def list_sync_status(self) -> dict:
        records = {str(item.get("kind") or ""): item for item in self.store.list("sync_status")}
        return {
            "success": True,
            "sync": {
                "health_accounts": records.get("health_accounts"),
                "results": records.get("results"),
                "agent": records.get("agent"),
            },
        }

    def dashboard_snapshot(self) -> dict:
        """Return one manager-local snapshot for the WebUI without contacting workers."""

        view = copy.copy(self)
        view.store = self.store.read_view(excluded_fields=frozenset({
            "memory_context", "memory_records", "memory_usages",
        }), copy_on_read=False)
        view._progress_collections = {}
        return view._dashboard_snapshot()

    def dashboard_details(self, section: str = "all") -> dict:
        """Load receipt audit, Agent conversation and repair history on demand."""
        view = copy.copy(self)
        view.store = self.store.read_view(excluded_fields=frozenset({
            "memory_context", "memory_records", "memory_usages",
        }), copy_on_read=False)
        result = {"success": True}
        if section in {"all", "audit"}:
            result["result_history"] = view.list_results()["result_history"]
        if section in {"all", "messages"}:
            result["agent_messages"] = view.list_agent_messages()["agent_messages"]
        if section in {"all", "debug"}:
            result["debug"] = view.debug_action("list")
        return result

    def _progress_records(self, collection: str) -> list[dict]:
        """Reuse read-only progress inputs within one dashboard response."""
        cache = getattr(self, "_progress_collections", None)
        if cache is None:
            return self.store.list(collection)
        if collection not in cache:
            cache[collection] = self.store.list(collection)
        return cache[collection]

    def _dashboard_snapshot(self) -> dict:
        machines_payload = self.list_machines()
        accounts_payload = self.list_accounts()
        alerts_payload = self.list_account_alerts()
        sync_payload = self.list_sync_status()
        tasks_payload = self.list_tasks()
        schedules_payload = self.list_schedules()
        reports_payload = self.list_operation_reports()
        transfers_payload = self.list_transfers()
        results_payload = self.list_results()
        personas_payload = self.list_personas()
        goals_payload = self.list_goals()
        operation_tasks_payload = self.list_operation_tasks()
        employee_summary = EmployeeOperations(
            self._employee_view_state(),
            results_payload["current_results"],
            self._result_metric_counts,
        ).summary(accounts_payload["accounts"])

        machines = machines_payload["machines"]
        tasks = tasks_payload["tasks"]
        accounts = employee_summary["accounts"]
        alerts = alerts_payload["alerts"]
        sync = sync_payload["sync"]
        active_task_states = {
            "ACCEPTED",
            "ACKNOWLEDGED",
            "RUNNING",
            "PAUSE_REQUESTED",
        }
        reconciliation_states = {
            "DISPATCH_UNKNOWN",
            "RECONCILIATION_REQUIRED",
            "RESULT_UNKNOWN",
        }

        def data_source(kind: str, label: str) -> dict:
            record = sync.get(kind) or {}
            return {
                "id": kind,
                "label": label,
                "status": str(record.get("status") or "NEVER"),
                "updated_at": record.get("updated_at") or record.get("completed_at"),
            }

        return {
            "success": True,
            "generated_at": utc_now(),
            "fleet": {
                "machine_total": len(machines),
                "ready_total": sum(item.get("status") == "READY" for item in machines),
                "account_total": len(accounts),
            },
            "overall": {
                "active_task_total": sum(
                    str(
                        item.get("business_state")
                        or item.get("state")
                        or item.get("dispatch_state")
                        or ""
                    ).upper()
                    in active_task_states
                    for item in tasks
                ),
                "reconciliation_total": sum(
                    any(
                        str(item.get(field) or "").upper() in reconciliation_states
                        for field in ("dispatch_state", "business_state")
                    )
                    for item in tasks
                ),
                "alert_total": len(alerts),
            },
            "collection": sync,
            "polling": self.get_polling_settings(),
            "machines": machines,
            "tasks": tasks,
            "schedules": schedules_payload["schedules"],
            "operation_reports": reports_payload["reports"],
            "transfers": transfers_payload["transfers"],
            "details_deferred": True,
            "results": results_payload["current_results"],
            "result_history": [],
            "incremental_records": results_payload["incremental_records"],
            "result_summary": results_payload["summary"],
            "accounts": accounts,
            "alerts": alerts,
            "personas": personas_payload["personas"],
            "goals": goals_payload["goals"],
            "daily_plans": self.list_daily_plans()["plans"],
            "employee_summary": employee_summary,
            "acceptance": self.operations_acceptance(),
            "operation_tasks": operation_tasks_payload["operation_tasks"],
            "agent_messages": [],
            "versions": self.store.list("versions"),
            "data_sources": [
                data_source("health_accounts", "机器与账号缓存"),
                data_source("results", "员工机结果回收"),
                data_source("agent", "Agent 任务状态"),
            ],
        }

    def record_sync_status(self, kind: str, values: dict) -> dict:
        normalized = str(kind or "").strip().lower()
        if normalized not in {"health_accounts", "results", "agent"}:
            raise ValueError("未知经理机同步类型")
        record = {
            **values,
            "kind": normalized,
            "record_id": f"sync:{normalized}",
            "updated_at": utc_now(),
        }
        return self.store.put("sync_status", normalized, record)

    def list_agent_messages(self, operation_task_id: str | None = None) -> dict:
        normalized_operation_id = str(operation_task_id or "").strip().upper()
        messages = self.store.list("agent_messages")
        if normalized_operation_id:
            messages = [
                item
                for item in messages
                if item.get("operation_task_id") == normalized_operation_id
            ]
        messages.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        return {"success": True, "agent_messages": messages, "total": len(messages)}

    def record_agent_message(self, request: dict) -> dict:
        operation_task_id = str(request.get("operation_task_id") or "").strip().upper()
        if operation_task_id:
            self._get_operation_task(operation_task_id)
        from_agent = str(request.get("from_agent") or "").strip()
        to_agent = str(request.get("to_agent") or "").strip()
        message_type = str(request.get("message_type") or "").strip().upper()
        if not from_agent or not to_agent or not message_type:
            raise ServiceError(
                "INVALID_AGENT_MESSAGE",
                "Agent交接消息必须包含发送方、接收方和消息类型",
            )
        message_id = str(request.get("message_id") or uuid.uuid4().hex).strip()
        payload = request.get("payload") or {}
        if not isinstance(payload, dict):
            raise ServiceError("INVALID_AGENT_MESSAGE", "Agent交接消息payload必须是JSON对象")
        record = {
            "message_id": message_id,
            "record_id": f"agent-message:{message_id}",
            "operation_task_id": operation_task_id,
            "task_id": str(request.get("task_id") or "").strip(),
            "from_agent": from_agent,
            "to_agent": to_agent,
            "message_type": message_type,
            "status": str(request.get("status") or "").strip().upper(),
            "summary": str(request.get("summary") or "").strip(),
            "payload": payload,
            "created_at": str(request.get("created_at") or utc_now()),
        }
        saved = self.store.put("agent_messages", message_id, record)
        if payload.get("memory_records") and record["task_id"]:
            task = self.store.get("tasks", record["task_id"]) or {}
            if task.get("machine_id"):
                self.ingest_memory({
                    "task_id": record["task_id"], "machine_id": task["machine_id"],
                    "remote_execution_id": payload.get("memory_session_id")
                    or task.get("remote_execution_id") or record["task_id"],
                    "memory_records": payload["memory_records"],
                })
        return {"success": True, "agent_message": saved}

    def list_daily_plans(self) -> dict:
        plans = sorted(self.store.list("daily_plans"), key=lambda item: item["plan_date"])
        return {"success": True, "plans": plans}

    def generate_daily_plan(self, request: dict) -> dict:
        day = parse_plan_date(request.get("plan_date"))
        overrides = {
            self._account_key(r["machine_id"], r["account_slot"]): r
            for r in request.get("accounts") or []
        }
        operations = EmployeeOperations(
            self.store.snapshot(),
            self.list_results()["current_results"],
            self._result_metric_counts,
        )
        accounts = [operations.enrich_account(a, day) for a in self.list_accounts()["accounts"]]
        rows = [
            generate_account(
                day, a, overrides.get(self._account_key(a["machine_id"], a["account_slot"]))
            )
            for a in accounts
        ]
        return {"success": True, "plan": {"plan_date": day, "accounts": rows, "status": "DRAFT"}}

    def preview_daily_plan(self, request: dict) -> dict:
        day = parse_plan_date(request.get("plan_date"))
        plan = self.store.get("daily_plans", day)
        if not plan:
            raise ServiceError("PLAN_NOT_FOUND", "请先保存每日计划", 404)
        if request.get("version") != plan["version"]:
            raise ServiceError("PLAN_VERSION_CHANGED", "计划版本已更新，请重新读取后提交", 409)
        submissions = plan.get("submissions") or []
        if submissions and all(row["version"] == plan["version"] for row in submissions):
            return {
                "success": True,
                "plan_date": day,
                "version": plan["version"],
                "materials": [
                    self._get_operation_task(row["operation_task_id"])["daily_material"]
                    for row in submissions
                ],
                "submissions": submissions,
            }
        operations = EmployeeOperations(
            self.store.snapshot(),
            self.list_results()["current_results"],
            self._result_metric_counts,
        )
        accounts = {
            self._account_key(a["machine_id"], a["account_slot"]): operations.enrich_account(a, day)
            for a in self.list_accounts()["accounts"]
        }
        materials = []
        for row in plan["accounts"]:
            if not row["participating"]:
                continue
            key = self._account_key(row["machine_id"], row["account_slot"])
            account = accounts.get(key) or {"health_state": "UNKNOWN"}
            persona = self._active_persona(row["machine_id"], row["account_slot"])
            materials.append(
                material(plan, row, account, self._persona_snapshot(persona) if persona else None)
            )
            materials[-1]["baseline_actual"] = {
                name: value["actual"] or 0
                for name, value in operations.day_metrics(
                    row["machine_id"], row["account_slot"], day
                ).items()
                if name in materials[-1]["budgets"]
            }
        return {
            "success": True,
            "plan_date": day,
            "version": plan["version"],
            "materials": materials,
            "submissions": plan.get("submissions", []),
        }

    def submit_daily_plan(self, request: dict) -> dict:
        """Explicitly hand the saved version to the manager Agent execution queue."""
        preview = self.preview_daily_plan(request)
        day, version = preview["plan_date"], preview["version"]
        previous = preview["submissions"]
        if previous:
            if all(row["version"] == version for row in previous):
                if request.get("action_inputs"):
                    for row in previous:
                        key = self._account_key(row["machine_id"], row["account_slot"])
                        self.supply_daily_inputs(
                            row["operation_task_id"],
                            {"action_inputs": (request["action_inputs"].get(key) or {})},
                        )
                return {"success": True, "status": "IDEMPOTENT", "submissions": previous}
            raise ServiceError(
                "PLAN_ALREADY_SUBMITTED", "该日期已提交，请在原任务中核对剩余预算", 409
            )
        if not preview["materials"]:
            raise ServiceError("PLAN_EMPTY", "计划没有参与运营的账号")
        blockers = [reason for item in preview["materials"] for reason in item["blockers"]]
        if blockers:
            raise ServiceError("PLAN_NOT_READY", "；".join(dict.fromkeys(blockers)), 409)
        now = utc_now()
        records = []
        for item in preview["materials"]:
            target = {k: item["identity"][k] for k in ("machine_id", "account_slot")}
            key = self._account_key(target["machine_id"], target["account_slot"])
            inputs = self._daily_inputs((request.get("action_inputs") or {}).get(key, {}), item)
            identifier = f"DAILY-{uuid.uuid4().hex[:16].upper()}"
            goal = {
                "goal_id": identifier,
                "version": 1,
                "state": "ACTIVE",
                "title": f"{day} {target['machine_id']}/{target['account_slot']} 每日运营",
                "period_start": day,
                "period_end": day,
                "targets": [target],
                "target_metrics": item["hard_targets"],
                "daily_operation_task_id": identifier,
                "desired_outcome": "逐项核销每日预算，关注博主按真实机会执行",
                "persona_bindings": [item["persona_snapshot"]],
                "created_at": now,
                "updated_at": now,
            }
            task = {
                "operation_task_id": identifier,
                "goal_id": identifier,
                "title": goal["title"],
                "targets": [target],
                "goal_snapshot": self._goal_snapshot(goal),
                "persona_bindings": goal["persona_bindings"],
                "daily_material": item,
                "plan_date": day,
                "action_inputs": {key: inputs},
                "source_type": "daily_plan",
                "requested_by": str(request.get("requested_by") or "经理用户"),
                "state": "WAITING_AGENT",
                "manager_agent_id": "",
                "plan": {},
                "created_at": now,
                "updated_at": now,
            }
            records.append((goal, task))

        def save(state: dict) -> list[dict]:
            plan = state["daily_plans"][day]
            if plan["version"] != version:
                raise ServiceError("PLAN_VERSION_CHANGED", "计划版本已更新，请重新读取后提交", 409)
            if plan.get("submissions"):
                return copy.deepcopy(plan["submissions"])
            submissions = []
            for goal, task in records:
                identifier = task["operation_task_id"]
                state["goals"][identifier] = goal
                state["operation_tasks"][identifier] = task
                submissions.append(
                    {
                        "operation_task_id": identifier,
                        "version": version,
                        **task["targets"][0],
                        "submitted_at": now,
                    }
                )
            plan.update(status="SUBMITTED", submissions=submissions)
            return copy.deepcopy(submissions)

        return {"success": True, "status": "SUBMITTED", "submissions": self.store.mutate(save)}

    def employee_summary(self) -> dict:
        return EmployeeOperations(
            self._employee_view_state(),
            self.list_results()["current_results"],
            self._result_metric_counts,
        ).summary(self.list_accounts()["accounts"])

    def _employee_view_state(self) -> dict:
        from manager_database import ManagerDatabase

        state = self.store.snapshot()
        state["worker_observations"] = ManagerDatabase(self.store.root).employee_observations()
        return state

    def save_account_operations(self, machine_id: str, account: str, request: dict) -> dict:
        machine_id = self._get_machine(machine_id)["machine_id"]
        key = self._account_key(machine_id, account)
        existing = self.store.get("accounts", key)
        if existing is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", "账号尚未进入经理机账号目录", 404)
        config = normalize_config(request, existing.get("operations_config") or {})
        config["updated_at"] = utc_now()
        self.store.update("accounts", key, lambda row: {**row, "operations_config": config})
        return {
            "success": True,
            "machine_id": machine_id,
            "account_slot": account,
            "operations_config": config,
        }

    def prepare_daily_plan(self, plan_date: str, request: dict) -> dict:
        """Compatibility entrypoint using the same versioned submission transaction."""
        plan = self.store.get("daily_plans", parse_plan_date(plan_date)) or {}
        result = self.submit_daily_plan(
            {
                **request,
                "plan_date": plan_date,
                "version": request.get("version", plan.get("version")),
            }
        )
        return {
            **result,
            "plan_date": plan_date,
            "operation_tasks": [
                self.get_operation_task(row["operation_task_id"])["operation_task"]
                for row in result["submissions"]
            ],
        }

    @staticmethod
    def _daily_inputs(inputs: dict, spec: dict) -> dict:
        if not isinstance(inputs, dict):
            raise ServiceError("INVALID_DAILY_INPUTS", "动作输入应为对象")
        ManagerService._validate_no_private_credentials(inputs)
        for metric, entries in inputs.items():
            if metric not in {"publish", "reply", "friend_request"} or not isinstance(
                entries, list
            ):
                raise ServiceError("INVALID_DAILY_INPUTS", "请按发布、回复或关注博主提供动作数组")
            if len(entries) > spec["budgets"][metric] or any(
                not isinstance(e, dict) for e in entries
            ):
                raise ServiceError("INVALID_DAILY_INPUTS", "动作输入应为对象且不能超过计划预算")
            if metric == "friend_request":
                warm = {c.get("user_id") for c in spec["evidence"].get("warm_candidates") or []}
                if any(
                    e.get("user_id") not in warm or e.get("user_id") == spec["identity"]["uid"]
                    for e in entries
                ):
                    raise ServiceError(
                        "WARM_TARGET_REQUIRED", "关注博主的目标需要该账号的暖互动证据"
                    )
        return copy.deepcopy(inputs)

    def supply_daily_inputs(self, operation_task_id: str, request: dict) -> dict:
        """Append reviewed targets/materials without changing earlier inputs or budgets."""

        def append(state: dict) -> dict:
            task = state["operation_tasks"].get(operation_task_id)
            if not task or not task.get("daily_material"):
                raise ServiceError("DAILY_TASK_NOT_FOUND", "每日运营任务不存在", 404)
            spec = task["daily_material"]
            key = self._account_key(
                spec["identity"]["machine_id"], spec["identity"]["account_slot"]
            )
            supplied = self._daily_inputs(request.get("action_inputs") or {}, spec)
            current = task.setdefault("action_inputs", {}).setdefault(key, {})
            if all(current.get(k) == v for k, v in supplied.items()):
                return copy.deepcopy(task)
            if task["state"] in {"COMPLETED", "BLOCKED"}:
                raise ServiceError("DAILY_TASK_FINAL", "每日任务已结束，不能追加动作输入", 409)
            for metric, entries in supplied.items():
                old = current.get(metric) or []
                if entries[: len(old)] != old:
                    raise ServiceError(
                        "DAILY_INPUT_FROZEN", "已登记动作输入保持不变，请追加新项", 409
                    )
                current[metric] = entries
            task["updated_at"] = utc_now()
            return copy.deepcopy(task)

        return {"success": True, "operation_task": self.store.mutate(append)}

    def save_daily_plan(self, request: dict) -> dict:
        """Save dated account quantities for subsequent Agent planning."""
        try:
            plan_date = date.fromisoformat(str(request.get("plan_date") or "")).isoformat()
        except ValueError as exc:
            raise ServiceError("INVALID_PLAN_DATE", "请选择有效的计划日期") from exc
        entries = request.get("accounts")
        if not isinstance(entries, list):
            raise ServiceError("INVALID_PLAN", "计划需要包含账号列表")
        accounts = []
        seen = set()
        previous = self.store.get("daily_plans", plan_date) or {}
        for entry in entries:
            machine = self._get_machine(str(entry.get("machine_id") or ""))
            slot = str(entry.get("account_slot") or "").strip()
            key = self._account_key(machine["machine_id"], slot)
            if key in seen or not self.store.get("accounts", key):
                raise ServiceError("INVALID_PLAN_ACCOUNT", "请选择已登记且不重复的员工机账号")
            seen.add(key)
            old_row = next(
                (
                    row
                    for row in previous.get("accounts", [])
                    if row["machine_id"] == machine["machine_id"] and row["account_slot"] == slot
                ),
                {},
            )
            metrics = dict(old_row.get("metrics") or {})
            for kind in PLAN_METRICS:
                value = (entry.get("metrics") or {}).get(kind, metrics.get(kind))
                if value is not None and (type(value) is not int or value < 0):
                    raise ServiceError("INVALID_PLAN_COUNT", "操作数量必须为非负整数")
                metrics[kind] = value
            accounts.append(
                {
                    "machine_id": machine["machine_id"],
                    "account_slot": slot,
                    "participating": entry.get("participating") is True,
                    "metrics": metrics,
                    "notes": str(entry.get("notes") or "").strip(),
                    "stage": str(entry.get("stage", old_row.get("stage", "")) or "").strip(),
                    "pressure": str(
                        entry.get("pressure", old_row.get("pressure", "")) or ""
                    ).strip(),
                }
            )
        plan = self.store.put(
            "daily_plans",
            plan_date,
            {
                "plan_date": plan_date,
                "accounts": accounts,
                "status": "SAVED",
                "version": int(previous.get("version", 0)) + 1,
                "updated_at": utc_now(),
                "submissions": previous.get("submissions", []),
                "history": [
                    *previous.get("history", []),
                    *([{k: v for k, v in previous.items() if k != "history"}] if previous else []),
                ],
            },
        )
        return {"success": True, "plan": plan}

    def manage_remote_account(self, machine_id: str, request: dict) -> dict:
        """Forward account lifecycle controls to the selected worker's local service."""
        machine = self.store.read_view(copy_on_read=False).get(
            "machines", str(machine_id).strip().upper()
        )
        if machine is None:
            raise ServiceError("MACHINE_NOT_FOUND", "执行机未注册", 404)
        try:
            response = self.transport.invoke(
                machine, "account-lifecycle", request, timeout_seconds=90
            )
        except ManagerTransportError as exc:
            raise ServiceError("ACCOUNT_ACTION_UNKNOWN", f"操作结果待核对：{exc}", 409) from exc
        remote_error = self._remote_error(response)
        if remote_error:
            code, message = remote_error
            raise ServiceError(code, message, 409)
        if request.get("action") == "delete" and response.get("archived"):
            key = self._account_key(machine["machine_id"], str(request.get("account") or ""))
            self.store.delete("accounts", key)
            self.store.delete("account_alerts", key)
        return {"success": True, "operation": response, "machine_id": machine["machine_id"]}

    def save_persona(self, request: dict) -> dict:
        """Create or update the stable persona configuration bound to an account unit."""

        normalized = self._normalize_persona_request(request)
        identity = self.memory_identity(normalized["machine_id"], normalized["account_slot"])
        from account_memory import namespace

        normalized["identity"] = identity
        normalized["memory_namespace"] = namespace(identity) if identity["uid"] else ""
        persona_id = normalized.pop("persona_id")
        account_key = self._account_key(
            normalized["machine_id"],
            normalized["account_slot"],
        )
        existing = self.store.get("personas", persona_id) if persona_id else None
        if existing is None and not persona_id:
            existing = next(
                (
                    item
                    for item in self.store.list("personas")
                    if item.get("account_key") == account_key
                ),
                None,
            )
        if existing is not None:
            persona_id = str(existing["persona_id"])
        else:
            persona_id = persona_id or f"PERSONA-{uuid.uuid4().hex[:10].upper()}"
        conflicting = next(
            (
                item
                for item in self.store.list("personas")
                if item.get("account_key") == account_key and item.get("persona_id") != persona_id
            ),
            None,
        )
        if conflicting is not None:
            raise ServiceError(
                "PERSONA_BINDING_EXISTS",
                "该账号已经绑定人设卡，请更新现有人设卡",
                409,
            )

        now = utc_now()
        previous_versions = list((existing or {}).get("versions") or [])
        previous_definition = self._persona_definition(existing or {})
        if existing is not None and previous_definition == normalized:
            return {"success": True, "status": "IDEMPOTENT", "persona": existing}
        version = int((existing or {}).get("version") or 0) + 1
        snapshot = {
            "persona_id": persona_id,
            "version": version,
            **normalized,
            "saved_at": now,
            "effective_at": now,
        }
        record = {
            **(existing or {}),
            **normalized,
            "persona_id": persona_id,
            "record_id": f"persona:{persona_id}",
            "account_key": account_key,
            "version": version,
            "versions": [*previous_versions, snapshot],
            "effective_at": now,
            "data_status": "observed",
            "missing_reason": "",
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
        }
        saved = self.store.put("personas", persona_id, record)
        return {
            "success": True,
            "status": "UPDATED" if existing is not None else "CREATED",
            "persona": saved,
        }

    def list_personas(self) -> dict:
        personas = sorted(
            self.store.list("personas"),
            key=lambda item: (
                str(item.get("machine_id") or ""),
                str(item.get("account_slot") or "").casefold(),
            ),
        )
        return {
            "success": True,
            "personas": personas,
            "summary": {
                "total": len(personas),
                "active": sum(item.get("status") == "ACTIVE" for item in personas),
            },
        }

    def get_persona(self, persona_id: str) -> dict:
        return {"success": True, "persona": self._get_persona(persona_id)}

    def save_goal(self, request: dict) -> dict:
        """Save an operating goal without creating a schedule or dispatching a task."""

        normalized = self._normalize_goal_request(request)
        goal_id = normalized.pop("goal_id") or f"GOAL-{uuid.uuid4().hex[:10].upper()}"
        existing = self.store.get("goals", goal_id)
        if existing is not None and existing.get("state") not in {"DRAFT", "PAUSED"}:
            raise ServiceError(
                "GOAL_NOT_EDITABLE",
                "运营目标需要先暂停再修改",
                409,
            )
        if existing is not None and self._goal_definition(existing) == normalized:
            return {"success": True, "status": "IDEMPOTENT", "goal": existing}

        now = utc_now()
        version = int((existing or {}).get("version") or 0) + 1
        snapshot = {
            "goal_id": goal_id,
            "version": version,
            **normalized,
            "saved_at": now,
        }
        record = {
            **(existing or {}),
            **normalized,
            "goal_id": goal_id,
            "record_id": f"goal:{goal_id}",
            "version": version,
            "versions": [*(existing or {}).get("versions", []), snapshot],
            "state": str((existing or {}).get("state") or "DRAFT"),
            "persona_bindings": list((existing or {}).get("persona_bindings") or []),
            "data_status": "observed",
            "missing_reason": "",
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
        }
        saved = self.store.put("goals", goal_id, record)
        return {
            "success": True,
            "status": "UPDATED" if existing is not None else "CREATED",
            "goal": self._with_goal_progress(saved),
        }

    def list_goals(self) -> dict:
        goals = [self._with_goal_progress(item) for item in self.store.list("goals")]
        goals.sort(key=lambda item: (item.get("period_start") or "", item.get("created_at") or ""))
        counts = Counter(str(item.get("state") or "DRAFT") for item in goals)
        return {
            "success": True,
            "goals": goals,
            "summary": {
                "total": len(goals),
                **{state: int(counts[state]) for state in sorted(GOAL_STATES)},
            },
        }

    def get_goal(self, goal_id: str) -> dict:
        return {"success": True, "goal": self._with_goal_progress(self._get_goal(goal_id))}

    def activate_goal(self, goal_id: str) -> dict:
        goal = self._get_goal(goal_id)
        if goal.get("state") == "ACTIVE":
            return {"success": True, "status": "ACTIVE", "goal": self._with_goal_progress(goal)}
        persona_bindings = []
        for target in goal["targets"]:
            persona = self._active_persona(target["machine_id"], target["account_slot"])
            if persona is None:
                raise ServiceError(
                    "PERSONA_REQUIRED",
                    f"{target['machine_id']} / {target['account_slot']} 尚未配置启用中的人设卡",
                    409,
                )
            persona_bindings.append(self._persona_snapshot(persona))
        updated = self._update_goal(
            goal_id,
            state="ACTIVE",
            persona_bindings=persona_bindings,
            activated_at=utc_now(),
            completed_at="",
        )
        return {"success": True, "status": "ACTIVE", "goal": self._with_goal_progress(updated)}

    def pause_goal(self, goal_id: str) -> dict:
        updated = self._update_goal(goal_id, state="PAUSED", paused_at=utc_now())
        return {"success": True, "status": "PAUSED", "goal": self._with_goal_progress(updated)}

    def complete_goal(self, goal_id: str) -> dict:
        updated = self._update_goal(goal_id, state="COMPLETED", completed_at=utc_now())
        return {
            "success": True,
            "status": "COMPLETED",
            "goal": self._with_goal_progress(updated),
        }

    def create_operation_task(self, request: dict) -> dict:
        """Create a WebUI work order for the manager Agent without dispatching it."""

        normalized = self._normalize_operation_task_request(request)
        operation_task_id = normalized.pop("operation_task_id") or (
            f"OPERATION-{uuid.uuid4().hex[:10].upper()}"
        )
        existing = self.store.get("operation_tasks", operation_task_id)
        goal = self._get_goal(normalized["goal_id"])
        if goal.get("state") != "ACTIVE":
            raise ServiceError("GOAL_NOT_ACTIVE", "运营目标需要先启用再提交运营任务", 409)
        targets = normalized.pop("targets") or list(goal.get("targets") or [])
        for target in targets:
            if not self._goal_has_target(
                goal,
                str(target.get("machine_id") or ""),
                str(target.get("account_slot") or ""),
            ):
                raise ServiceError("GOAL_TARGET_MISMATCH", "运营任务账号不在目标范围内", 409)
        persona_bindings = [
            dict(persona)
            for persona in goal.get("persona_bindings") or []
            if any(
                self._account_key(
                    str(persona.get("machine_id") or ""),
                    str(persona.get("account_slot") or ""),
                )
                == self._account_key(
                    str(target.get("machine_id") or ""),
                    str(target.get("account_slot") or ""),
                )
                for target in targets
            )
        ]
        if len(persona_bindings) != len(targets):
            raise ServiceError("PERSONA_REQUIRED", "运营任务缺少目标账号的人设快照", 409)
        definition = {
            **normalized,
            "title": normalized["title"] or str(goal.get("title") or "运营任务"),
            "completion_criteria": normalized["completion_criteria"]
            or str(goal.get("desired_outcome") or "按运营目标完成并回传结果"),
            "targets": targets,
            "goal_snapshot": self._goal_snapshot(goal),
            "persona_bindings": persona_bindings,
        }
        if existing is not None:
            if self._operation_task_definition(existing) == definition:
                return {
                    "success": True,
                    "status": "IDEMPOTENT",
                    "operation_task": self._with_operation_task_progress(existing),
                }
            raise ServiceError("DUPLICATE_OPERATION_TASK_ID", "运营任务编号已被使用", 409)
        now = utc_now()
        record = {
            "operation_task_id": operation_task_id,
            "record_id": f"operation-task:{operation_task_id}",
            **definition,
            "source_type": "webui",
            "state": "WAITING_AGENT",
            "manager_agent_id": "",
            "plan": {},
            "schedule_ids": [],
            "execution_task_ids": [],
            "result_ids": [],
            "last_result_state": "",
            "result_summary": "",
            "data_status": "observed",
            "missing_reason": "",
            "claimed_at": "",
            "planned_at": "",
            "completed_at": "",
            "created_at": now,
            "updated_at": now,
        }
        saved = self.store.put("operation_tasks", operation_task_id, record)
        return {
            "success": True,
            "status": "WAITING_AGENT",
            "operation_task": self._with_operation_task_progress(saved),
        }

    def list_operation_tasks(self, state: str | None = None) -> dict:
        normalized_state = str(state or "").strip().upper()
        if normalized_state and normalized_state not in OPERATION_TASK_STATES:
            raise ServiceError("INVALID_OPERATION_TASK_STATE", "未知运营任务状态")
        tasks = [
            self._with_operation_task_progress(item)
            for item in self.store.list("operation_tasks")
            if not normalized_state or item.get("state") == normalized_state
        ]
        tasks.sort(key=lambda item: item.get("created_at") or "", reverse=True)
        counts = Counter(str(item.get("state") or "WAITING_AGENT") for item in tasks)
        return {
            "success": True,
            "operation_tasks": tasks,
            "summary": {
                "total": len(tasks),
                **{name: int(counts[name]) for name in sorted(OPERATION_TASK_STATES)},
            },
        }

    def get_operation_task(self, operation_task_id: str) -> dict:
        task = self._get_operation_task(operation_task_id)
        return {"success": True, "operation_task": self._with_operation_task_progress(task)}

    def claim_operation_task(self, operation_task_id: str, request: dict) -> dict:
        task = self._get_operation_task(operation_task_id)
        manager_agent_id = str(request.get("manager_agent_id") or "").strip()
        if not manager_agent_id:
            raise ServiceError("INVALID_OPERATION_TASK", "领取任务必须提供经理Agent编号")
        current_agent = str(task.get("manager_agent_id") or "")
        if current_agent and current_agent != manager_agent_id:
            raise ServiceError("OPERATION_TASK_CLAIMED", "运营任务已由其他经理Agent领取", 409)
        if task.get("state") in {"COMPLETED", "BLOCKED"}:
            raise ServiceError("OPERATION_TASK_FINAL", "运营任务已经结束", 409)
        if current_agent == manager_agent_id and task.get("state") != "WAITING_AGENT":
            return {
                "success": True,
                "status": "IDEMPOTENT",
                "operation_task": self._with_operation_task_progress(task),
            }
        updated = self._update_operation_task(
            operation_task_id,
            state="PLANNING",
            manager_agent_id=manager_agent_id,
            claimed_at=utc_now(),
        )
        return {
            "success": True,
            "status": "PLANNING",
            "operation_task": self._with_operation_task_progress(updated),
        }

    def record_operation_plan(self, operation_task_id: str, request: dict) -> dict:
        task = self._claimed_operation_task(operation_task_id, request)
        raw_actions = request.get("planned_actions") or []
        if not isinstance(raw_actions, list):
            raise ServiceError("INVALID_OPERATION_PLAN", "运营计划动作必须是数组")
        plan = {
            "summary": str(request.get("plan_summary") or "").strip(),
            "planned_actions": [item for item in raw_actions if isinstance(item, dict)],
            "dispatch_failures": [
                item for item in (request.get("dispatch_failures") or []) if isinstance(item, dict)
            ],
            "recorded_by": task["manager_agent_id"],
            "recorded_at": utc_now(),
        }
        updated = self._update_operation_task(
            operation_task_id,
            state="PLANNING",
            plan=plan,
            planned_at=plan["recorded_at"],
        )
        return {
            "success": True,
            "status": "PLANNING",
            "operation_task": self._with_operation_task_progress(updated),
        }

    def finish_operation_task(self, operation_task_id: str, request: dict) -> dict:
        state = str(request.get("state") or "COMPLETED").strip().upper()
        if state not in {"COMPLETED", "BLOCKED"}:
            raise ServiceError("INVALID_OPERATION_TASK_STATE", "运营任务只能完成或标记阻塞")
        current = self._get_operation_task(operation_task_id)
        if current.get("state") == "BLOCKED" and state == "COMPLETED":
            manager_agent_id = str(request.get("manager_agent_id") or "").strip()
            if not manager_agent_id or manager_agent_id != current.get("manager_agent_id"):
                raise ServiceError(
                    "OPERATION_TASK_AGENT_MISMATCH",
                    "经理Agent编号与任务领取记录不一致",
                    409,
                )
            progress = self._with_operation_task_progress(current).get("progress") or {}
            target_metrics = progress.get("target_metrics") or {}
            actual_metrics = progress.get("actual_metrics") or {}
            missing = {
                name: int(target) - int(actual_metrics.get(name) or 0)
                for name, target in target_metrics.items()
                if int(actual_metrics.get(name) or 0) < int(target)
            }
            if missing:
                raise ServiceError(
                    "OPERATION_RESULTS_INCOMPLETE",
                    "运营任务回收指标尚未达到目标，不能完成对账",
                    409,
                )
        else:
            self._claimed_operation_task(operation_task_id, request)
        updated = self._update_operation_task(
            operation_task_id,
            state=state,
            result_summary=str(request.get("result_summary") or "").strip(),
            completed_at=utc_now(),
        )
        for schedule_id in updated.get("schedule_ids") or []:
            schedule = self.store.get("schedules", str(schedule_id))
            if schedule is not None and schedule.get("state") == "ACTIVE":
                self._update_schedule(str(schedule_id), state="PAUSED", paused_at=utc_now())
        return {
            "success": True,
            "status": state,
            "operation_task": self._with_operation_task_progress(updated),
        }

    def list_machine_accounts(self, machine_id: str) -> dict:
        """Read all remote accounts over SSH and refresh the manager cache."""

        machine = self._get_machine(machine_id)
        checked_at = utc_now()
        try:
            response = self.transport.invoke(machine, "list-accounts", {}, timeout_seconds=60)
        except ManagerTransportError as exc:
            self._mark_machine_accounts_unknown(
                machine["machine_id"], exc.code, str(exc), checked_at
            )
            self._update_machine(
                machine["machine_id"],
                last_account_sync_at=checked_at,
                account_sync_status="UNKNOWN",
                account_sync_error_code=exc.code,
                account_sync_error=str(exc),
            )
            cached = self.list_accounts(machine["machine_id"])
            return {
                **cached,
                "success": False,
                "status": "UNKNOWN",
                "message": str(exc),
            }
        remote_error = self._remote_error(response)
        if remote_error:
            error_code, message = remote_error
            self._mark_machine_accounts_unknown(
                machine["machine_id"], error_code, message, checked_at
            )
            self._update_machine(
                machine["machine_id"],
                last_account_sync_at=checked_at,
                account_sync_status="UNKNOWN",
                account_sync_error_code=error_code,
                account_sync_error=message,
            )
            cached = self.list_accounts(machine["machine_id"])
            return {
                **cached,
                "success": False,
                "status": "UNKNOWN",
                "message": message,
            }
        remote_accounts = response.get("accounts")
        if not isinstance(remote_accounts, list):
            self._update_machine(
                machine["machine_id"],
                last_account_sync_at=checked_at,
                account_sync_status="UNKNOWN",
                account_sync_error_code="INVALID_REMOTE_RESPONSE",
                account_sync_error="执行机账号清单返回格式无效",
            )
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机账号清单返回格式无效")
        seen: set[str] = set()
        for item in remote_accounts:
            if not isinstance(item, dict):
                continue
            record = self._save_account_snapshot(
                machine["machine_id"],
                item,
                checked_at=str(response.get("checked_at") or checked_at),
            )
            seen.add(record["account_key"])
        self._mark_unreturned_accounts_unknown(machine["machine_id"], seen, checked_at)
        cached = self.list_accounts(machine["machine_id"])
        self._update_machine(
            machine["machine_id"],
            last_account_sync_at=checked_at,
            last_account_check_at=checked_at,
            account_summary=cached["summary"],
            account_sync_status="SUCCESS",
            account_sync_error_code="",
            account_sync_error="",
        )
        return {"success": True, "status": "READY", **cached}

    def get_machine_account_status(self, machine_id: str, account: str) -> dict:
        return self._invoke_account_request(machine_id, account, "account-status", {})

    def doctor_machine_account(self, machine_id: str, account: str) -> dict:
        return self._invoke_account_request(machine_id, account, "account-doctor", {})

    def run_machine_account_action(
        self,
        machine_id: str,
        account: str,
        action: str,
    ) -> dict:
        return self._invoke_account_request(
            machine_id,
            account,
            "account-action",
            {"account_action": action},
            timeout_seconds=90,
        )

    def request_machine_account_field_action(
        self,
        machine_id: str,
        account: str,
        request: dict,
    ) -> dict:
        request_type = str(request.get("request_type") or "LOGIN").strip().upper()
        if request_type not in {"LOGIN", "IDENTITY_CHECK"}:
            raise ServiceError("INVALID_REQUEST_TYPE", "不支持的现场处理类型")
        request_id = str(request.get("request_id") or "").strip()
        if not request_id:
            request_id = f"ACCOUNT-{uuid.uuid4().hex[:12].upper()}"
        return self._invoke_account_request(
            machine_id,
            account,
            "account-field-request",
            {
                "request_id": request_id,
                "request_type": request_type,
                "instructions": str(request.get("instructions") or "").strip(),
            },
            timeout_seconds=30,
        )

    def check_machine(self, machine_id: str) -> dict:
        machine = self._get_machine(machine_id)
        checked_at = utc_now()
        try:
            response = self.transport.invoke(machine, "health", {}, timeout_seconds=20)
        except ManagerTransportError as exc:
            status = "CHECK_UNKNOWN" if exc.result_unknown else "OFFLINE"
            self._mark_machine_accounts_unknown(
                machine["machine_id"], exc.code, str(exc), checked_at
            )
            updated = self._update_machine(
                machine["machine_id"],
                status=status,
                last_check_at=checked_at,
                ssh_state="CHECK_UNKNOWN" if exc.result_unknown else "OFFLINE",
                last_error_code=exc.code,
                last_error=str(exc),
            )
            return {"success": False, "status": status, "machine": updated}
        return self._record_machine_health(machine, response, checked_at=checked_at)

    def machine_executor_status(self, machine_id: str) -> dict:
        return self._control_machine_service(machine_id, "executor-status")

    def start_machine_executor(self, machine_id: str) -> dict:
        return self._control_machine_service(machine_id, "start-executor")

    def restart_machine_executor(self, machine_id: str) -> dict:
        return self._control_machine_service(machine_id, "restart-executor")

    def machine_webui_status(self, machine_id: str) -> dict:
        return self._control_machine_service(machine_id, "webui-status")

    def start_machine_webui(self, machine_id: str) -> dict:
        return self._control_machine_service(machine_id, "start-webui")

    def restart_machine_webui(self, machine_id: str) -> dict:
        return self._control_machine_service(machine_id, "restart-webui")

    def _control_machine_service(self, machine_id: str, action: str) -> dict:
        machine = self._get_machine(machine_id)
        checked_at = utc_now()
        try:
            response = self.transport.invoke(machine, action, {}, timeout_seconds=45)
        except ManagerTransportError as exc:
            status = "CHECK_UNKNOWN" if exc.result_unknown else "OFFLINE"
            updated = self._update_machine(
                machine["machine_id"],
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
            health = response.get("health")
            values = {
                "status": "BLOCKED",
                "last_check_at": checked_at,
                "ssh_state": "ONLINE",
                "last_error_code": error_code,
                "last_error": message,
            }
            if isinstance(health, dict):
                values["last_health"] = dict(health)
            updated = self._update_machine(machine["machine_id"], **values)
            return {
                "success": False,
                "status": "BLOCKED",
                "message": message,
                "machine": updated,
                "operation": response.get("operation"),
            }

        result = self._record_machine_health(machine, response, checked_at=checked_at)
        result["operation"] = response.get("operation")
        service_status = response.get("executor_service") or response.get("webui")
        result["executor_service"] = service_status
        result["webui"] = response.get("webui") or service_status
        result["message"] = response.get("message") or result["status"]
        return result

    def _record_machine_health(
        self,
        machine: dict,
        response: dict,
        *,
        checked_at: str,
    ) -> dict:
        machine_id = machine["machine_id"]

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
        exchange = health.get("task_exchange")
        if isinstance(exchange, dict):
            inbox_root = str(exchange.get("inbox_root") or "")
            outbox_root = str(exchange.get("outbox_root") or "")
            values["observed_task_inbox_root"] = inbox_root
            values["observed_task_outbox_root"] = outbox_root
            if inbox_root and not machine.get("task_inbox_root"):
                values["task_inbox_root"] = inbox_root
            if outbox_root and not machine.get("task_outbox_root"):
                values["task_outbox_root"] = outbox_root
        if status in {"READY", "PAUSED"}:
            values["last_seen_at"] = checked_at
        updated = self._update_machine(machine_id, **values)
        self._record_health_account_slots(machine_id, health, checked_at)
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
        account_key = self._account_key(machine["machine_id"], normalized["account_slot"])
        managed_account = self.store.get("accounts", account_key)
        if (
            existing is None
            and managed_account is not None
            and managed_account.get("scheduling_enabled") is False
        ):
            raise ServiceError(
                "ACCOUNT_SCHEDULING_DISABLED",
                "目标账号已在经理机暂停调度",
                409,
            )
        normalized = self._attach_task_context(normalized)
        if existing is None:
            if normalized["capability"] == "post-comment" and not (
                normalized.get("persona_snapshot") or normalized["parameters"].get("persona")
            ):
                persona = next((p for p in self.store.list("personas")
                    if p.get("machine_id") == machine["machine_id"]
                    and p.get("account_slot") == normalized["account_slot"]
                    and p.get("status") == "ACTIVE"), None)
                if persona is None:
                    from default_persona import friendly_commenter

                    persona = friendly_commenter(machine["machine_id"], normalized["account_slot"])
                normalized["parameters"]["persona"] = self._persona_snapshot(persona)
            config = (managed_account or {}).get("operations_config") or {}
            if config:
                check_operations_policy(
                    config,
                    normalized["capability"],
                    self._publish_policy_results(
                        machine["machine_id"], normalized["account_slot"]
                    ) if normalized["capability"] in PUBLISH_ACTIONS else [],
                    account_state=str(
                        (managed_account or {}).get("error_code")
                        or (managed_account or {}).get("raw_status")
                        or (managed_account or {}).get("health_state")
                        or ""
                    ),
                    write_intent=normalized["write_intent"],
                    now=(datetime.fromisoformat(normalized["parameters"]["scheduled_at"])
                         if normalized["parameters"].get("scheduled_at") else None),
                )
                if not normalized["parameters"].get("daily_plan"):
                    normalized["parameters"] = {
                        **normalized["parameters"], "operations_config": config
                    }
            self._validate_scheduled_dispatch(normalized)
        if existing is not None:
            saved_persona = (existing["request"].get("parameters") or {}).get("persona")
            if saved_persona and not normalized["parameters"].get("persona"):
                normalized["parameters"]["persona"] = copy.deepcopy(saved_persona)
            previous_policy = (existing["request"].get("parameters") or {}).get("operations_config")
            if previous_policy and "operations_config" not in normalized["parameters"]:
                normalized["parameters"] = {
                    **normalized["parameters"],
                    "operations_config": previous_policy,
                }
            existing_request = self._normalize_task_request(existing["request"])
            for field in ("goal_snapshot", "persona_snapshot"):
                existing_request[field] = copy.deepcopy(existing["request"].get(field) or {})
            if existing_request != normalized:
                raise ServiceError("DUPLICATE_TASK_ID", "任务编号已被不同请求使用", 409)
            if existing_request != existing["request"]:
                existing = self._update_task(task_id, request=existing_request)
            if existing.get("reconciliation_required"):
                return {
                    "success": False,
                    "status": "RECONCILIATION_REQUIRED",
                    "task": existing,
                }
            if existing["dispatch_state"] == "PAUSED":
                return {"success": True, "status": "PAUSED", "task": existing}
            if existing["dispatch_state"] not in {"PENDING_DISPATCH", "TRANSFERRED"}:
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
            if normalized["operation_task_id"]:
                self._link_operation_execution_task(
                    normalized["operation_task_id"],
                    task_id,
                )

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
        if self._uses_sftp_task_exchange(machine):
            return self._dispatch_task_via_exchange(record, machine, envelope)
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

        self._record_remote_agent_messages(accepted)
        execution = self._extract_execution(accepted)
        record = self._update_task_from_execution(task_id, execution, connectivity="ONLINE")
        if execution.get("state") in REMOTE_TERMINAL_STATES:
            return {"success": True, "status": execution["state"], "task": record}

        try:
            started = self.transport.invoke(
                machine,
                "start-task",
                {
                    "task_id": task_id,
                    "remote_execution_id": record.get("remote_execution_id"),
                },
                timeout_seconds=30,
            )
        except ManagerTransportError as exc:
            record = self._update_task(
                task_id,
                dispatch_state="RECONCILIATION_REQUIRED",
                connectivity="CONTACT_LOST",
                reconciliation_required=True,
                error_code=exc.code,
                message="员工Agent启动确认中断，必须先读取执行机状态",
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
            "status": str(started.get("status") or "TASK_ACK"),
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

    def register_schedule(self, request: dict) -> dict:
        normalized = self._normalize_schedule_request(request)
        self._get_machine(normalized["machine_id"])
        if normalized["operation_task_id"]:
            operation_task, goal_snapshot, persona_snapshot = self._operation_task_context(
                normalized["operation_task_id"],
                normalized["machine_id"],
                normalized["account_slot"],
            )
            if normalized["goal_id"] and normalized["goal_id"] != operation_task["goal_id"]:
                raise ServiceError("GOAL_TARGET_MISMATCH", "计划目标与运营任务不一致", 409)
            normalized["goal_id"] = str(operation_task["goal_id"])
            normalized["goal_snapshot"] = goal_snapshot
            normalized["persona_snapshot"] = persona_snapshot
        elif normalized["goal_id"]:
            goal_snapshot, persona_snapshot = self._goal_context(
                normalized["goal_id"],
                normalized["machine_id"],
                normalized["account_slot"],
            )
            normalized["goal_snapshot"] = goal_snapshot
            normalized["persona_snapshot"] = persona_snapshot
        schedule_id = normalized["schedule_id"]
        existing = self.store.get("schedules", schedule_id)
        now = utc_now()
        if existing is not None:
            if self._schedule_definition(existing) == self._schedule_definition(normalized):
                return {"success": True, "status": "IDEMPOTENT", "schedule": existing}
            record = {
                **existing,
                **normalized,
                "state": str(request.get("state") or existing.get("state") or "ACTIVE")
                .strip()
                .upper(),
                "revision": int(existing.get("revision") or 1) + 1,
                "updated_at": now,
            }
        else:
            record = {
                **normalized,
                "record_id": f"schedule:{schedule_id}",
                "data_status": "observed",
                "missing_reason": "",
                "source_type": "agent",
                "state": str(request.get("state") or "ACTIVE").strip().upper(),
                "revision": 1,
                "last_task_id": "",
                "last_run_at": "",
                "last_result_state": "",
                "recent_runs": [],
                "created_at": now,
                "updated_at": now,
            }
        if record["state"] not in {"ACTIVE", "PAUSED"}:
            raise ServiceError("INVALID_SCHEDULE", "调度计划状态必须是ACTIVE或PAUSED")
        saved = self.store.put("schedules", schedule_id, record)
        if normalized["operation_task_id"]:
            self._link_operation_schedule(normalized["operation_task_id"], schedule_id)
        return {
            "success": True,
            "status": "UPDATED" if existing is not None else "REGISTERED",
            "schedule": saved,
        }

    def list_schedules(self) -> dict:
        schedules = sorted(
            self.store.list("schedules"),
            key=lambda item: (item.get("next_run_at") or "", item.get("schedule_id") or ""),
        )
        return {"success": True, "schedules": schedules}

    def pause_schedule(self, schedule_id: str) -> dict:
        schedule = self._get_schedule(schedule_id)
        if schedule["state"] == "PAUSED":
            return {"success": True, "status": "PAUSED", "schedule": schedule}
        updated = self._update_schedule(
            schedule_id,
            state="PAUSED",
            paused_at=utc_now(),
        )
        return {"success": True, "status": "PAUSED", "schedule": updated}

    def resume_schedule(self, schedule_id: str) -> dict:
        schedule = self._get_schedule(schedule_id)
        if schedule.get("operation_task_id"):
            self._operation_task_context(
                str(schedule["operation_task_id"]),
                str(schedule["machine_id"]),
                str(schedule["account_slot"]),
            )
        elif schedule.get("goal_id"):
            self._goal_context(
                str(schedule["goal_id"]),
                str(schedule["machine_id"]),
                str(schedule["account_slot"]),
            )
        if schedule["state"] == "ACTIVE":
            return {"success": True, "status": "ACTIVE", "schedule": schedule}
        updated = self._update_schedule(
            schedule_id,
            state="ACTIVE",
            paused_at="",
        )
        return {"success": True, "status": "ACTIVE", "schedule": updated}

    def record_schedule_run(self, schedule_id: str, request: dict) -> dict:
        schedule = self._get_schedule(schedule_id)
        task_id = str(request.get("task_id") or "").strip()
        state = str(request.get("state") or "").strip().upper()
        if not task_id or not state:
            raise ServiceError("INVALID_SCHEDULE_RUN", "调度运行记录缺少task_id或state")
        run = {
            "task_id": task_id,
            "state": state,
            "result_summary": str(request.get("result_summary") or "").strip(),
            "recorded_at": str(request.get("recorded_at") or utc_now()),
        }
        recent = [
            item for item in schedule.get("recent_runs") or [] if item.get("task_id") != task_id
        ]
        recent.insert(0, run)
        values = {
            "last_task_id": task_id,
            "last_run_at": run["recorded_at"],
            "last_result_state": state,
            "recent_runs": recent[:10],
        }
        if "next_run_at" in request:
            values["next_run_at"] = self._normalize_timestamp(
                request.get("next_run_at"),
                "next_run_at",
            )
        updated = self._update_schedule(schedule_id, **values)
        return {"success": True, "status": "RECORDED", "schedule": updated}

    def list_operation_reports(self) -> dict:
        reports = sorted(
            self.store.list("operation_reports"),
            key=lambda item: item.get("created_at") or "",
            reverse=True,
        )
        return {"success": True, "reports": reports}

    def generate_operation_report(self, request: dict) -> dict:
        definition = self._normalize_report_request(request)
        if definition["machine_id"]:
            self._get_machine(definition["machine_id"])
        return {
            "success": True,
            "status": "GENERATED",
            "report": self._create_operation_report(definition),
        }

    def revise_operation_report(self, report_id: str, request: dict) -> dict:
        previous = self.store.get("operation_reports", str(report_id).strip())
        if previous is None:
            raise ServiceError("REPORT_NOT_FOUND", "经理机运营报告不存在", 404)
        reason = str(request.get("revision_reason") or "").strip()
        if not reason:
            raise ServiceError("INVALID_REPORT", "修订原因不能为空")
        root_id = str(previous.get("root_report_id") or previous["report_id"])
        revisions = [
            item
            for item in self.store.list("operation_reports")
            if str(item.get("root_report_id") or item.get("report_id")) == root_id
        ]
        revision_number = max(int(item.get("revision_number") or 1) for item in revisions) + 1
        report = self._create_operation_report(
            dict(previous["definition"]),
            root_report_id=root_id,
            previous_report_id=previous["report_id"],
            revision_number=revision_number,
            revision_reason=reason,
        )
        return {"success": True, "status": "REVISED", "report": report}

    def list_transfers(self) -> dict:
        transfers = sorted(
            self.store.list("transfers"),
            key=lambda item: item["created_at"],
            reverse=True,
        )
        return {"success": True, "transfers": transfers}

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
        view = self.store.read_view(copy_on_read=False)
        results = view.list("results")
        incremental_records = view.list("incremental_records")
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
        latest_by_execution: dict[str, dict] = {}
        for result in results:
            execution_key = self._result_execution_key(result)
            current = latest_by_execution.get(execution_key)
            if current is None or self._result_revision_key(result) > self._result_revision_key(
                current
            ):
                latest_by_execution[execution_key] = result
        current_results = sorted(
            latest_by_execution.values(),
            key=self._result_revision_key,
            reverse=True,
        )
        result_history = [
            {
                **item,
                "is_current_revision": latest_by_execution[self._result_execution_key(item)]
                is item,
            }
            for item in results
        ]
        attributions = {
            f"{row['record_id']}:{row['item_index']}": row
            for row in view.list("action_attributions")
        }
        return {
            "success": True,
            "results": results,
            "current_results": [
                self._with_action_attributions(row, attributions) for row in current_results
            ],
            "result_history": result_history,
            "incremental_records": incremental_records,
            "summary": self._summarize_results(current_results),
            "incremental_unique_count": len(incremental_records),
        }

    def _publish_policy_results(self, machine_id: str, account_slot: str) -> list[dict]:
        """Read the current publish revisions needed for one account's dispatch policy."""
        rows = self.store.read_view(copy_on_read=False).list("results")
        latest: dict[str, dict] = {}
        for row in rows:
            if row.get("machine_id") != machine_id or self._result_account(row) != account_slot:
                continue
            request = row.get("task_request") or row.get("request") or {}
            if (row.get("capability") or request.get("capability")) not in PUBLISH_ACTIONS:
                continue
            key = self._result_execution_key(row)
            previous = latest.get(key)
            if previous is None or self._result_revision_key(row) > self._result_revision_key(
                previous
            ):
                latest[key] = row
        return list(latest.values())

    def _with_action_attributions(self, result: dict, attributions: dict) -> dict:
        record = copy.deepcopy(result)
        items = record.get("item_results") or [record]
        for index, item in enumerate(items):
            annotation = attributions.get(f"{record.get('record_id')}:{index}")
            if annotation:
                item["execution_source"] = annotation["execution_source"]
                item["attribution_updated_at"] = annotation["updated_at"]
        return record

    def save_action_attribution(self, record_id: str, request: dict) -> dict:
        result = next(
            (r for r in self.store.list("results") if r.get("record_id") == record_id), None
        )
        if result is None:
            raise ServiceError("NOT_FOUND", "执行回执不存在", 404)
        index = request.get("item_index", 0)
        items = result.get("item_results") or [result]
        if type(index) is not int or not 0 <= index < len(items):
            raise ServiceError("INVALID_ACTION_ITEM", "请选择有效的动作记录")
        source = request.get("execution_source")
        if source not in {"machine", "human"}:
            raise ServiceError("INVALID_EXECUTION_SOURCE", "执行者只能选择机器或人工")
        key = f"{record_id}:{index}"
        previous = self.store.get("action_attributions", key) or {}
        annotation = {
            "record_id": record_id,
            "item_index": index,
            "execution_source": source,
            "updated_at": utc_now(),
            "previous_source": previous.get("execution_source")
            or items[index].get("execution_source", "machine"),
        }
        self.store.put("action_attributions", key, annotation)
        return {"success": True, "attribution": annotation}

    @staticmethod
    def _result_execution_key(result: dict) -> str:
        return ":".join(
            [
                str(result.get("machine_id") or ""),
                str(
                    result.get("remote_execution_id")
                    or result.get("task_id")
                    or result.get("record_id")
                    or ""
                ),
            ]
        )

    @staticmethod
    def _result_revision_key(result: dict) -> tuple[int, int, str]:
        revision = result.get("result_revision")
        try:
            revision_number = int(revision)
        except (TypeError, ValueError):
            revision_number = 0
        return (
            revision_number,
            int(result.get("sequence") or 0),
            str(result.get("collected_at") or result.get("recorded_at") or ""),
        )

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

    def query_business_data(self, payload: dict | None = None) -> dict:
        """Discover every stored field or execute a read-only manager SQL query."""
        from manager_database import ManagerDatabase

        database = ManagerDatabase(self.store.root)
        self.store._transaction(lambda state: database.sync(state), write=False)
        if payload is None:
            return database.schema()
        return database.query(str(payload.get("sql") or ""), payload.get("parameters"),
                              int(payload.get("limit", 200)))

    def operations_acceptance(self, payload: dict | None = None) -> dict:
        from operations_acceptance import acceptance_report

        payload = payload or {}
        return acceptance_report(self.store.snapshot(), day=str(payload.get("date") or ""),
                                 accounts=payload.get("accounts"))

    def collect_business_data(self, machine_id: str) -> dict:
        """Import complete employee business tables into the manager query database."""
        from manager_database import ManagerDatabase

        machine = self._get_machine(machine_id)
        database = ManagerDatabase(self.store.root)
        self.store._transaction(lambda state: database.sync(state), write=False)
        snapshot = self.transport.export_business_data(machine)
        return database.import_worker(machine["machine_id"], snapshot)

    def collect_results(self, machine_id: str, *, download_evidence: bool = True) -> dict:
        collection_view = self.store.read_view(copy_on_read=False)
        machine = collection_view.get("machines", machine_id.strip().upper())
        if not machine:
            raise ServiceError("MACHINE_NOT_FOUND", "执行机未注册", 404)
        machine_id = machine["machine_id"]
        if self._uses_sftp_task_exchange(machine) and callable(
            getattr(self.transport, "sftp_download", None)
        ):
            return self._collect_sftp_outbox(
                machine, download_evidence=download_evidence, collection_view=collection_view
            )
        sync_at = utc_now()
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
            self._update_machine(
                machine_id,
                last_result_sync_at=sync_at,
                result_sync_status="RESULT_UNKNOWN" if exc.result_unknown else "OFFLINE",
                result_sync_error_code=exc.code,
                result_sync_error=str(exc),
            )
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
            self._update_machine(
                machine_id,
                last_result_sync_at=sync_at,
                result_sync_status="BLOCKED",
                result_sync_error_code=error_code,
                result_sync_error=message,
            )
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
        ingested = self._ingest_results(
            machine,
            results,
            download_evidence=download_evidence,
            evidence_errors=evidence_errors,
        )
        normalized_results = ingested["results"]
        inserted = ingested["collected"]
        duplicates = ingested["duplicates"]
        incremental_inserted = ingested["incremental_collected"]
        incremental_duplicates = ingested["incremental_duplicates"]
        evidence_errors = ingested["evidence_errors"]
        next_sequence = max(
            cursor,
            int(response.get("next_sequence") or 0),
            *(int(item.get("sequence") or 0) for item in results),
        )
        self.store.set_collection_cursor(machine_id, next_sequence)
        summary = self._summarize_results(normalized_results)
        status = "PARTIAL_SUCCESS" if evidence_errors else "SUCCESS"
        self._update_machine(
            machine_id,
            last_result_sync_at=sync_at,
            last_result_success_at=utc_now(),
            result_sync_status=status,
            result_sync_error_code=(
                str(evidence_errors[0].get("error_code") or "") if evidence_errors else ""
            ),
            result_sync_error=(
                str(evidence_errors[0].get("message") or "") if evidence_errors else ""
            ),
        )
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
            "results": normalized_results,
        }

    def _collect_sftp_outbox(
        self, machine: dict, *, download_evidence: bool, collection_view: ManagerStore | None = None
    ) -> dict:
        machine_id = str(machine["machine_id"])
        sync_at = utc_now()
        cursor_key = f"{machine_id}:sftp_outbox"
        cursor = (collection_view or self.store).get_collection_cursor(cursor_key)
        outbox_root = str(
            machine.get("task_outbox_root") or machine.get("observed_task_outbox_root") or ""
        )
        collector_root = self.store.root / "collector" / self._safe_path_component(machine_id)
        index_path = collector_root / "index.json"
        remote_index = self._join_remote_path(outbox_root, "index.json")
        try:
            self.transport.sftp_download(
                machine,
                remote_index,
                index_path,
                timeout_seconds=30,
            )
            index = json.loads(index_path.read_text(encoding="utf-8-sig"))
            if not isinstance(index, dict) or not isinstance(index.get("records"), list):
                raise ValueError("outbox index is not a JSON object with records")
            if str(index.get("machine_id") or "").casefold() != machine_id.casefold():
                raise ValueError("outbox index machine id mismatch")
        except ManagerTransportError as exc:
            self._update_machine(
                machine_id,
                last_result_sync_at=sync_at,
                result_sync_status="RESULT_UNKNOWN" if exc.result_unknown else "OFFLINE",
                result_sync_error_code=exc.code,
                result_sync_error=str(exc),
            )
            return {
                "success": False,
                "status": "RESULT_UNKNOWN" if exc.result_unknown else "OFFLINE",
                "machine_id": machine_id,
                "error_code": exc.code,
                "message": str(exc),
                "collected": 0,
                "incremental_collected": 0,
                "pending_evidence": self._pending_evidence_count(machine_id),
            }
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            self._update_machine(
                machine_id,
                last_result_sync_at=sync_at,
                result_sync_status="BLOCKED",
                result_sync_error_code="INVALID_OUTBOX_INDEX",
                result_sync_error=str(exc),
            )
            return {
                "success": False,
                "status": "BLOCKED",
                "machine_id": machine_id,
                "error_code": "INVALID_OUTBOX_INDEX",
                "message": "员工机 outbox 索引无法读取",
                "collected": 0,
                "incremental_collected": 0,
                "pending_evidence": self._pending_evidence_count(machine_id),
            }

        records = sorted(
            (
                item
                for item in index["records"]
                if isinstance(item, dict) and int(item.get("sequence") or 0) > cursor
            ),
            key=lambda item: int(item.get("sequence") or 0),
        )
        collected_results = 0
        duplicate_results = 0
        incremental_collected = 0
        incremental_duplicates = 0
        acknowledgement_count = 0
        event_count = 0
        evidence_errors = self._retry_pending_evidence(machine) if download_evidence else []
        normalized_results: list[dict] = []
        next_cursor = cursor
        collection_error = ""
        archive_root = (
            self.store.root
            / "daily_receipts"
            / datetime.now().astimezone().date().isoformat()
            / "raw"
            / self._safe_path_component(machine_id)
        )

        def archive_receipt(metadata: dict, local_record: Path) -> None:
            target = (
                archive_root
                / self._safe_path_component(str(metadata.get("task_id") or "unassigned"))
                / f"{int(metadata['sequence']):012d}.json"
            )
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(local_record, target)

        # Archive receipts collected before the dated archive layout was introduced.
        if not machine.get("outbox_archive_initialized"):
            for metadata in index["records"]:
                if (
                    not isinstance(metadata, dict)
                    or not 0 < int(metadata.get("sequence") or 0) <= cursor
                ):
                    continue
                cached = collector_root / "records" / f"{int(metadata['sequence']):012d}.json"
                if cached.exists():
                    try:
                        archive_receipt(metadata, cached)
                    except OSError as exc:
                        collection_error = f"回执 {metadata['sequence']} 归档失败：{exc}"
                        break
        batch_download = getattr(self.transport, "sftp_download_many", None)
        if callable(batch_download):
            files = []
            for metadata in records[:OUTBOX_COLLECTION_BATCH_SIZE]:
                if not metadata.get("relative_path") or metadata.get("record_type") not in {
                    "acknowledgement",
                    "event",
                    "result",
                    "debug_incident",
                    "debug_receipt",
                }:
                    break
                sequence = int(metadata.get("sequence") or 0)
                files.append(
                    (
                        self._join_remote_path(outbox_root, str(metadata["relative_path"])),
                        collector_root / "records" / f"{sequence:012d}.json",
                    )
                )
            # Completed immutable files remain available; the cursor advances in order.
            if files:
                with suppress(ManagerTransportError):
                    batch_download(machine, files, timeout_seconds=60)
        for metadata in records[:OUTBOX_COLLECTION_BATCH_SIZE]:
            sequence = int(metadata.get("sequence") or 0)
            relative_path = str(metadata.get("relative_path") or "")
            record_type = str(metadata.get("record_type") or "")
            if not relative_path or record_type not in {
                "acknowledgement", "event", "result", "debug_incident", "debug_receipt"
            }:
                collection_error = f"回执 {sequence} 的文件路径或类型无法识别"
                break
            local_record = collector_root / "records" / f"{sequence:012d}.json"
            remote_record = self._join_remote_path(outbox_root, relative_path)
            try:
                if not callable(batch_download):
                    self.transport.sftp_download(
                        machine,
                        remote_record,
                        local_record,
                        timeout_seconds=30,
                    )
                record = json.loads(local_record.read_text(encoding="utf-8-sig"))
                if not isinstance(record, dict) or int(record.get("sequence") or 0) != sequence:
                    raise ValueError("outbox record sequence mismatch")
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    raise ValueError("outbox record payload missing")
                archive_receipt(metadata, local_record)
            except (ManagerTransportError, OSError, json.JSONDecodeError, ValueError) as exc:
                collection_error = f"回执 {sequence}（{relative_path}）收集失败：{exc}"
                break

            if record_type in {"debug_incident", "debug_receipt"}:
                from manager_debug import ManagerDebugService

                debug = ManagerDebugService(self)
                try:
                    if record_type == "debug_incident":
                        debug.ingest_incident(machine_id, payload["incident"])
                    else:
                        debug.ingest_receipt(machine_id, payload["receipt"])
                except (ServiceError, KeyError) as exc:
                    collection_error = f"调试回执 {sequence} 导入失败：{exc}"
                    break
                event_count += 1
            elif record_type == "result":
                raw_result = payload.get("result")
                if not isinstance(raw_result, dict):
                    collection_error = f"回执 {sequence} 缺少任务结果"
                    break
                ingested = self._ingest_results(
                    machine,
                    [raw_result],
                    download_evidence=download_evidence,
                    evidence_errors=evidence_errors,
                )
                evidence_errors = ingested["evidence_errors"]
                collected_results += int(ingested["collected"])
                duplicate_results += int(ingested["duplicates"])
                incremental_collected += int(ingested["incremental_collected"])
                incremental_duplicates += int(ingested["incremental_duplicates"])
                normalized_results.extend(ingested["results"])
                for collected_result in ingested["results"]:
                    task_id = str(collected_result.get("task_id") or "")
                    task = self.store.get("tasks", task_id) if task_id else None
                    if isinstance(task, dict):
                        self._record_result_ack(
                            machine,
                            task,
                            collected_result,
                            collection_mode="sftp_outbox",
                        )
            elif record_type == "event":
                with self.store.batch():
                    message = payload.get("agent_message")
                    if isinstance(message, dict):
                        self.record_agent_message(message)
                    execution = payload.get("execution")
                    task_id = str(metadata.get("task_id") or "")
                    manager_task = self.store.get("tasks", task_id) if task_id else None
                    if isinstance(execution, dict) and manager_task:
                        self._update_task_from_execution(
                            task_id,
                            execution,
                            connectivity="ONLINE",
                        )
                    if (
                        manager_task
                        and isinstance(message, dict)
                        and str(message.get("status") or "") == "RUNNING"
                    ):
                        self._update_task(
                            str(metadata["task_id"]),
                            dispatch_state="RUNNING",
                            last_known_remote_state="RUNNING",
                            connectivity="ONLINE",
                            message=str(message.get("summary") or "员工 Agent 正在执行任务"),
                        )
                event_count += 1
            else:
                with self.store.batch():
                    for message in payload.get("agent_messages") or []:
                        if isinstance(message, dict):
                            self.record_agent_message(message)
                    task_id = str(payload.get("task_id") or metadata.get("task_id") or "")
                    if task_id and self.store.get("tasks", task_id):
                        self._update_task(
                            task_id,
                            dispatch_state="ACKNOWLEDGED",
                            remote_execution_id=str(
                                payload.get("remote_execution_id")
                                or metadata.get("remote_execution_id")
                                or ""
                            ),
                            last_known_remote_state="ACCEPTED",
                            connectivity="ONLINE",
                            reconciliation_required=False,
                            error_code="",
                            message="已从员工机 outbox 回收到任务 ACK",
                        )
                acknowledgement_count += 1
            next_cursor = sequence

        if next_cursor > cursor:
            self.store.set_collection_cursor(cursor_key, next_cursor)

        status = (
            "PARTIAL_SUCCESS" if next_cursor < int(index.get("high_watermark") or 0) else "SUCCESS"
        )
        if not records:
            status = "IDLE"
        if collection_error:
            status = "PARTIAL_SUCCESS"
        all_records = [item for item in index["records"] if isinstance(item, dict)]
        def record_task_key(item: dict) -> str:
            return str(
                item.get("remote_execution_id")
                or item.get("task_id")
                or f"record:{item.get('sequence')}"
            )

        task_keys = {record_task_key(item) for item in all_records}
        imported_task_keys = {
            record_task_key(item)
            for item in all_records
            if int(item.get("sequence") or 0) <= next_cursor
        }
        outbox_collection = {
            "observed_at": sync_at,
            "remote_updated_at": index.get("updated_at"),
            "total_records": len(all_records),
            "total_tasks": len(task_keys),
            "imported_records": sum(
                int(item.get("sequence") or 0) <= next_cursor for item in all_records
            ),
            "imported_tasks": len(imported_task_keys),
            "pending_records": sum(
                int(item.get("sequence") or 0) > next_cursor for item in all_records
            ),
            "pending_tasks": len(task_keys - imported_task_keys),
            "cursor": next_cursor,
            "high_watermark": int(index.get("high_watermark") or 0),
            "batch_records": (
                acknowledgement_count + event_count + collected_results + duplicate_results
            ),
            "batch_results": collected_results,
            "batch_events": event_count,
            "batch_acknowledgements": acknowledgement_count,
            "archive_root": str(archive_root),
        }
        self._update_machine(
            machine_id,
            last_result_sync_at=sync_at,
            last_result_success_at=(
                machine.get("last_result_success_at") if collection_error else utc_now()
            ),
            result_sync_status=status,
            result_sync_error_code="OUTBOX_COLLECTION_FAILED" if collection_error else "",
            result_sync_error=collection_error,
            outbox_collection=outbox_collection,
            outbox_archive_initialized=not collection_error,
        )
        summary = self._summarize_results(normalized_results)
        return {
            "success": not collection_error,
            "status": status,
            "machine_id": machine_id,
            "transport": "sftp_outbox",
            "error_code": "OUTBOX_COLLECTION_FAILED" if collection_error else "",
            "message": collection_error,
            "outbox_collection": outbox_collection,
            "previous_cursor": cursor,
            "next_cursor": next_cursor,
            "collected": collected_results,
            "duplicates": duplicate_results,
            "acknowledgements_collected": acknowledgement_count,
            "events_collected": event_count,
            "incremental_collected": incremental_collected,
            "incremental_duplicates": incremental_duplicates,
            "state_counts": summary["task_state_counts"],
            "summary": summary,
            "evidence_errors": evidence_errors,
            "pending_evidence": (
                sum(
                    1
                    for item in collection_view.list("evidence_downloads")
                    if item.get("machine_id") == machine_id and item.get("state") != "SUCCESS"
                )
                if collection_view is not None and not records and not download_evidence
                else self._pending_evidence_count(machine_id)
            ),
            "results": normalized_results,
        }

    def receive_agent_message(self, request: dict) -> dict:
        machine = self._get_machine(str(request.get("machine_id") or ""))
        message = request.get("agent_message")
        if not isinstance(message, dict):
            raise ServiceError("INVALID_AGENT_MESSAGE", "员工Agent回传缺少消息内容")
        task_id = str(message.get("task_id") or "")
        if task_id:
            task = self._get_task(task_id)
            if task.get("machine_id") != machine["machine_id"]:
                raise ServiceError("TASK_MACHINE_MISMATCH", "Agent消息与任务执行机不一致", 409)
        saved = self.record_agent_message(message)["agent_message"]
        return {
            "success": True,
            "status": "MESSAGE_ACK",
            "agent_message": saved,
        }

    def receive_task_result(self, request: dict) -> dict:
        machine = self._get_machine(str(request.get("machine_id") or ""))
        raw_result = request.get("result")
        if not isinstance(raw_result, dict):
            raise ServiceError("INVALID_RESULT", "员工Agent回传缺少任务结果")
        task = self._get_task(str(raw_result.get("task_id") or ""))
        if task.get("machine_id") != machine["machine_id"]:
            raise ServiceError("TASK_MACHINE_MISMATCH", "回传结果与任务执行机不一致", 409)
        expected_execution_id = str(task.get("remote_execution_id") or "")
        returned_execution_id = str(raw_result.get("remote_execution_id") or "")
        if expected_execution_id and returned_execution_id != expected_execution_id:
            raise ServiceError("EXECUTION_ID_MISMATCH", "回传结果与执行编号不一致", 409)
        message = request.get("agent_message")
        if isinstance(message, dict):
            self.record_agent_message(message)
        ingested = self._ingest_results(machine, [raw_result], download_evidence=False)
        sequence = int(raw_result.get("sequence") or 0)
        self.store.set_collection_cursor(machine["machine_id"], sequence)
        self._update_machine(
            machine["machine_id"],
            last_result_sync_at=utc_now(),
            last_result_success_at=utc_now(),
            result_sync_status="SUCCESS",
            result_sync_error_code="",
            result_sync_error="",
        )
        ack = self._record_result_ack(
            machine,
            task,
            raw_result,
            collection_mode="worker_callback",
        )
        return {
            "success": True,
            "status": "RESULT_ACK",
            "collected": ingested["collected"],
            "duplicates": ingested["duplicates"],
            "next_cursor": self.store.get_collection_cursor(machine["machine_id"]),
            "agent_message": ack,
        }

    def _record_result_ack(
        self,
        machine: dict,
        task: dict,
        result: dict,
        *,
        collection_mode: str,
    ) -> dict:
        operation_task_id = str((task.get("request") or {}).get("operation_task_id") or "")
        manager_agent_id = "MANAGER-AGENT-01"
        if operation_task_id:
            operation_task = self._get_operation_task(operation_task_id)
            manager_agent_id = str(operation_task.get("manager_agent_id") or manager_agent_id)
        revision = str(result.get("result_revision") or result.get("sequence") or "1")
        message_id = f"{task['dispatch_id']}:result-ack:{revision}"
        existing = self.store.get("agent_messages", message_id)
        if isinstance(existing, dict):
            return existing
        collected_at = str(result.get("collected_at") or utc_now())
        return self.record_agent_message(
            {
                "message_id": message_id,
                "operation_task_id": operation_task_id,
                "task_id": task["task_id"],
                "from_agent": manager_agent_id,
                "to_agent": f"{machine['machine_id']}-AGENT",
                "message_type": "RESULT_ACK",
                "status": str(result.get("state") or "UNKNOWN"),
                "summary": "经理Agent已回收并入库员工机任务结果",
                "payload": {
                    "stage": "result_collected",
                    "machine_id": machine["machine_id"],
                    "remote_execution_id": str(result.get("remote_execution_id") or ""),
                    "sequence": int(result.get("sequence") or 0),
                    "result_revision": revision,
                    "collection_mode": collection_mode,
                    "collected_at": collected_at,
                },
                "created_at": collected_at,
            }
        )["agent_message"]

    def _ingest_results(
        self,
        machine: dict,
        results: list[dict],
        *,
        download_evidence: bool,
        evidence_errors: list[dict] | None = None,
    ) -> dict:
        machine_id = machine["machine_id"]
        recorded_evidence_errors = list(evidence_errors or [])
        inserted = 0
        duplicates = 0
        incremental_inserted = 0
        incremental_duplicates = 0
        normalized_results: list[dict] = []
        for item in results:
            result = {**item, "machine_id": machine_id, "collected_at": utc_now()}
            task = self.store.get("tasks", str(result.get("task_id") or "")) or {}
            request = task.get("request") if isinstance(task.get("request"), dict) else {}
            if not task:
                business = result.get("business_result") or {}
                for field in ("account_slot", "platform", "capability"):
                    if not result.get(field) and business.get(field):
                        result[field] = business[field]
                result.setdefault("source_type", "executor")
            if task:
                goal_snapshot = (
                    request.get("goal_snapshot")
                    if isinstance(request.get("goal_snapshot"), dict)
                    else {}
                )
                persona_snapshot = (
                    request.get("persona_snapshot")
                    if isinstance(request.get("persona_snapshot"), dict)
                    else {}
                )
                result.update(
                    {
                        "account_slot": str(request.get("account_slot") or ""),
                        "capability": str(request.get("capability") or ""),
                        "source_type": str(request.get("source_type") or "agent"),
                        "task_request": dict(request),
                        "schedule_id": str(request.get("schedule_id") or ""),
                        "operation_task_id": str(request.get("operation_task_id") or ""),
                        "goal_id": str(request.get("goal_id") or ""),
                        "goal_version": int(goal_snapshot.get("version") or 0),
                        "persona_id": str(persona_snapshot.get("persona_id") or ""),
                        "persona_version": int(persona_snapshot.get("version") or 0),
                    }
                )
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
            with self.store.batch():
                _, created = self.store.record_result(result_key, result)
                from debug_common import incident_from_result
                from manager_debug import ManagerDebugService

                incident = incident_from_result(machine_id, result)
                if incident:
                    ManagerDebugService(self).ingest_incident(machine_id, incident)
                new_records, replayed_records = self._record_incremental_data(machine_id, result)
                self._apply_collected_result(result)
                self._record_operation_result(result)
                self.ingest_memory(result)
                for message in result.get("agent_messages") or []:
                    if isinstance(message, dict):
                        self.record_agent_message(message)
            inserted += int(created)
            duplicates += int(not created)
            incremental_inserted += new_records
            incremental_duplicates += replayed_records
            if download_evidence:
                recorded_evidence_errors.extend(self._download_evidence(machine, result))
            normalized_results.append(result)
        return {
            "collected": inserted,
            "duplicates": duplicates,
            "incremental_collected": incremental_inserted,
            "incremental_duplicates": incremental_duplicates,
            "evidence_errors": recorded_evidence_errors,
            "results": normalized_results,
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
                "account_slot": str(result.get("account_slot") or ""),
                "capability": str(result.get("capability") or ""),
                "schedule_id": str(result.get("schedule_id") or ""),
                "operation_task_id": str(result.get("operation_task_id") or ""),
                "goal_id": str(result.get("goal_id") or ""),
                "goal_version": int(result.get("goal_version") or 0),
                "persona_id": str(result.get("persona_id") or ""),
                "persona_version": int(result.get("persona_version") or 0),
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
        if int(task.get("reviewed_result_revision") or 0) > int(
            result.get("result_revision") or result.get("sequence") or 0
        ):
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
            error = self._attempt_evidence_download(machine, existing or record)
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
        if record.get("state") == "SUCCESS":
            return None
        retry_at = record.get("next_retry_at")
        if retry_at and datetime.fromisoformat(retry_at) > datetime.now(UTC):
            return {
                "index": int(record.get("index") or 0),
                "remote_path": str(record["remote_path"]),
                "error_code": record.get("error_code", ""),
                "message": record.get("message", ""),
                "next_retry_at": retry_at,
            }
        target = Path(str(record["destination"]))
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # A failed transfer must not leave a partial file that looks complete.
            with tempfile.TemporaryDirectory(prefix=".evidence-", dir=target.parent) as staging:
                downloaded = Path(staging) / "content"
                self.transport.download(
                    machine,
                    str(record["remote_path"]),
                    str(downloaded),
                    timeout_seconds=120,
                )
                evidence_type = "directory" if downloaded.is_dir() else "file"
                if downloaded.is_dir():
                    shutil.copytree(downloaded, target, dirs_exist_ok=True)
                else:
                    os.replace(downloaded, target)
        except ManagerTransportError as exc:
            attempts = int(record.get("attempt_count") or 0) + 1
            delay = 0 if attempts == 1 else min(21600, 600 * 2 ** min(attempts - 1, 6))
            pending = {
                **record,
                "state": "PENDING",
                "error_code": exc.code,
                "message": str(exc),
                "attempt_count": attempts,
                "next_retry_at": (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(),
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
                "evidence_type": evidence_type,
                "next_retry_at": None,
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
        business_result_statuses: Counter[str] = Counter()
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
                    item_state = str(item.get("state") or "").strip()
                    if item_state:
                        item_states[item_state] += 1
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

            business_status = str(
                result.get("business_result_status")
                or ("observed" if "business_result" in result else "missing")
            )
            business_result_statuses[business_status] += 1
            if business_status != "observed":
                data_gaps.append(
                    {
                        "task_id": str(result.get("task_id") or ""),
                        "data_type": "business_result",
                        "data_status": business_status,
                        "missing_reason": str(
                            result.get("business_result_missing_reason") or "未提供业务结果"
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
        business_status = ManagerService._combined_data_status(business_result_statuses)
        incremental_status = ManagerService._combined_data_status(incremental_data_statuses)
        return {
            "task_state_counts": dict(sorted(task_states.items())),
            "item_results_status": item_status,
            "item_results_data_status_counts": dict(sorted(item_data_statuses.items())),
            "item_state_counts": dict(sorted(item_states.items())),
            "business_result_status": business_status,
            "business_result_status_counts": dict(sorted(business_result_statuses.items())),
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
                for name in ("execution", "task", "transfer", "machine", "account")
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

    def _record_remote_agent_messages(self, response: dict) -> None:
        for message in response.get("agent_messages") or []:
            if isinstance(message, dict):
                self.record_agent_message(message)

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

    def _invoke_account_request(
        self,
        machine_id: str,
        account: str,
        action: str,
        payload: dict,
        *,
        timeout_seconds: float = 60,
    ) -> dict:
        machine = self._get_machine(machine_id)
        account_slot = str(account or "").strip()
        if not account_slot:
            raise ServiceError("INVALID_ACCOUNT", "账号槽位不能为空")
        checked_at = utc_now()
        try:
            response = self.transport.invoke(
                machine,
                action,
                {"account": account_slot, **payload},
                timeout_seconds=timeout_seconds,
            )
        except ManagerTransportError as exc:
            record = self._mark_account_unknown(
                machine["machine_id"], account_slot, exc.code, str(exc), checked_at
            )
            return {
                "success": False,
                "status": "UNKNOWN",
                "message": str(exc),
                "account": self._with_account_task(record),
            }
        remote_error = self._remote_error(response)
        remote_account = response.get("account")
        record = None
        if isinstance(remote_account, dict):
            record = self._save_account_snapshot(
                machine["machine_id"],
                remote_account,
                checked_at=str(response.get("checked_at") or checked_at),
            )
        if remote_error:
            error_code, message = remote_error
            if record is None or str(response.get("status") or "").upper() == "UNKNOWN":
                record = self._mark_account_unknown(
                    machine["machine_id"], account_slot, error_code, message, checked_at
                )
            return {
                "success": False,
                "status": record["health_state"],
                "message": message,
                "account": self._with_account_task(record),
                "diagnosis": response.get("diagnosis"),
                "operation": response.get("operation"),
            }
        if record is None:
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机账号状态返回格式无效")
        self._update_machine(machine["machine_id"], last_account_check_at=checked_at)
        return {
            "success": True,
            "status": record["health_state"],
            "account": self._with_account_task(record),
            "diagnosis": response.get("diagnosis"),
            "operation": response.get("operation"),
            "field_request": response.get("field_request"),
        }

    def _record_health_account_slots(
        self,
        machine_id: str,
        health: dict,
        checked_at: str,
    ) -> None:
        summary = health.get("account_slots")
        if not isinstance(summary, dict) or not isinstance(summary.get("items"), list):
            return
        for item in summary["items"]:
            if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                continue
            account_slot = str(item["name"]).strip()
            if any(
                name in item
                for name in (
                    "status",
                    "server_running",
                    "extension_connected",
                    "profile_verified",
                    "identity",
                )
            ):
                self._save_account_snapshot(machine_id, item, checked_at=checked_at)
                continue
            key = self._account_key(machine_id, account_slot)
            existing = self.store.get("accounts", key)
            if existing:
                updated = {
                    **existing,
                    **{
                        name: item[name]
                        for name in ("platform", "profile_mode", "paired")
                        if name in item
                    },
                    "last_seen_at": checked_at,
                    "updated_at": checked_at,
                }
                self.store.put("accounts", key, updated)
                continue
            self._save_account_snapshot(machine_id, item, checked_at=checked_at)

    def _save_account_snapshot(
        self,
        machine_id: str,
        snapshot: dict,
        *,
        checked_at: str,
    ) -> dict:
        account_slot = str(snapshot.get("name") or snapshot.get("account_slot") or "").strip()
        if not account_slot:
            raise ServiceError("INVALID_REMOTE_RESPONSE", "执行机账号记录缺少槽位名称")
        machine_id = str(machine_id).strip().upper()
        key = self._account_key(machine_id, account_slot)
        existing = self.store.get("accounts", key) or {}
        health_state, reason, recommended_action = self._normalize_account_health(snapshot)
        identity = snapshot.get("identity") if isinstance(snapshot.get("identity"), dict) else {}
        runtime = snapshot.get("runtime") if isinstance(snapshot.get("runtime"), dict) else {}
        now = utc_now()
        record = {
            **existing,
            "account_key": key,
            "record_id": f"account:{key}",
            "machine_id": machine_id,
            "account_slot": account_slot,
            "name": account_slot,
            "platform": str(snapshot.get("platform") or existing.get("platform") or "xiaohongshu"),
            "profile_mode": str(snapshot.get("profile_mode") or existing.get("profile_mode") or ""),
            "paired": bool(
                snapshot.get(
                    "paired",
                    snapshot.get(
                        "extension_instance_enrolled",
                        existing.get("paired", False),
                    ),
                )
            ),
            "health_state": health_state,
            "raw_status": str(snapshot.get("status") or "UNKNOWN").upper(),
            "bridge_status": self._signal_state(
                snapshot.get("server_running"), false_state="BLOCKED"
            ),
            "extension_status": self._signal_state(
                snapshot.get("extension_connected"), false_state="WARNING"
            ),
            "profile_status": self._signal_state(
                snapshot.get("profile_verified"), false_state="BLOCKED"
            ),
            "profile_running": runtime.get("profile_running"),
            "identity": dict(identity),
            "uid": str(identity.get("live_user_id") or identity.get("user_id") or ""),
            "nickname": str(identity.get("live_nickname") or identity.get("nickname") or ""),
            "account_note": str(existing.get("account_note") or ""),
            "scheduling_enabled": bool(existing.get("scheduling_enabled", True)),
            "management_source_label": str(existing.get("management_source_label") or "经理机配置"),
            "management_updated_at": existing.get("management_updated_at"),
            "field_request": copy.deepcopy(
                snapshot.get("field_request", existing.get("field_request"))
            ),
            "connection_ready": bool(snapshot.get("connection_ready")),
            "abnormal_reason": reason,
            "recommended_action": recommended_action,
            "error_code": str(snapshot.get("error_code") or ""),
            "last_error": str(snapshot.get("message") or ""),
            "raw_snapshot": dict(snapshot),
            "data_status": "observed" if health_state != "UNKNOWN" else "missing",
            "last_check_at": checked_at,
            "last_seen_at": checked_at,
            "created_at": existing.get("created_at") or now,
            "updated_at": now,
        }
        stored = self.store.put("accounts", key, record)
        self._sync_account_alert(stored)
        return stored

    @staticmethod
    def _normalize_account_health(snapshot: dict) -> tuple[str, str, str]:
        raw_status = str(snapshot.get("status") or "UNKNOWN").upper()
        error_code = str(snapshot.get("error_code") or "")
        if raw_status in {"UNKNOWN", "ERROR"} or error_code:
            message = str(snapshot.get("message") or "员工机账号状态无法读取")
            return (
                "UNKNOWN",
                message,
                str(snapshot.get("next_action") or "检查员工机连接和执行服务后重新读取"),
            )
        if snapshot.get("server_running") is False:
            return "BLOCKED", "Bridge 未运行", "重启 Bridge 后重新检查账号状态"
        if raw_status in ACCOUNT_BLOCKED_STATES:
            return (
                "BLOCKED",
                str(snapshot.get("message") or snapshot.get("next_action") or "账号当前不可执行"),
                str(snapshot.get("next_action") or "运行账号诊断并处理登录或身份问题"),
            )
        if snapshot.get("profile_verified") is False and snapshot.get("extension_connected"):
            return "BLOCKED", "连接的 Profile 与账号槽位不匹配", "检查 Profile 绑定和扩展配对"
        if (
            snapshot.get("extension_connected") is False
            or raw_status in ACCOUNT_WARNING_STATES
            or not snapshot.get("paired", snapshot.get("extension_instance_enrolled", True))
        ):
            return (
                "WARNING",
                str(snapshot.get("message") or snapshot.get("next_action") or "连接或身份待确认"),
                str(snapshot.get("next_action") or "完成扩展连接和身份核验"),
            )
        if raw_status == "READY" and snapshot.get("ready", True):
            return "READY", "", ""
        return (
            "WARNING",
            str(snapshot.get("message") or snapshot.get("next_action") or "账号状态待确认"),
            str(snapshot.get("next_action") or "重新检查账号状态"),
        )

    @staticmethod
    def _signal_state(value: object, *, false_state: str) -> str:
        if value is True:
            return "READY"
        if value is False:
            return false_state
        return "UNKNOWN"

    def _with_account_task(self, account: dict) -> dict:
        tasks = [
            item
            for item in self.store.list("tasks")
            if item.get("machine_id") == account.get("machine_id")
            and (item.get("request") or {}).get("account_slot") == account.get("account_slot")
        ]
        tasks.sort(key=lambda item: item.get("updated_at") or "", reverse=True)
        active = next(
            (
                item
                for item in tasks
                if str(item.get("business_state") or item.get("dispatch_state") or "")
                not in REMOTE_TERMINAL_STATES | {"COMPLETED", "CANCELLED"}
            ),
            None,
        )
        return {
            **account,
            "agent_task_id": str((active or {}).get("task_id") or ""),
            "agent_task_status": str(
                (active or {}).get("business_state")
                or (active or {}).get("dispatch_state")
                or "IDLE"
            ),
        }

    def _with_machine_freshness(self, machine: dict) -> dict:
        observed_status = str(machine.get("status") or "REGISTERED")
        observed_ssh_state = str(machine.get("ssh_state") or "UNVERIFIED")
        age = self._timestamp_age_seconds(machine.get("last_check_at"))
        freshness = "UNKNOWN" if age is None else "FRESH"
        stale = age is not None and age > self.machine_stale_seconds
        if stale:
            freshness = "STALE"
        return {
            **machine,
            "stored_status": observed_status,
            "stored_ssh_state": observed_ssh_state,
            "status": "UNKNOWN" if stale else observed_status,
            "ssh_state": "UNKNOWN" if stale else observed_ssh_state,
            "data_freshness": freshness,
            "sync_age_seconds": round(age, 1) if age is not None else None,
            "stale": stale,
            "stale_reason": (
                f"机器状态已超过 {self.machine_stale_seconds:g} 秒未更新" if stale else ""
            ),
        }

    def _with_account_freshness(self, account: dict) -> dict:
        observed_health_state = str(account.get("health_state") or "UNKNOWN")
        age = self._timestamp_age_seconds(account.get("updated_at"))
        freshness = "UNKNOWN" if age is None else "FRESH"
        stale = age is not None and age > self.account_stale_seconds
        if stale:
            freshness = "STALE"
        return {
            **account,
            "stored_health_state": observed_health_state,
            "health_state": "UNKNOWN" if stale else observed_health_state,
            "data_freshness": freshness,
            "sync_age_seconds": round(age, 1) if age is not None else None,
            "stale": stale,
            "stale_reason": (
                f"账号状态已超过 {self.account_stale_seconds:g} 秒未更新" if stale else ""
            ),
            "abnormal_reason": (
                "账号状态已过期，当前状态需要重新读取"
                if stale
                else str(account.get("abnormal_reason") or "")
            ),
            "recommended_action": (
                "等待后台同步或手动同步该员工机账号状态"
                if stale
                else str(account.get("recommended_action") or "")
            ),
        }

    @staticmethod
    def _timestamp_age_seconds(value: object) -> float | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            observed_at = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        return max(0.0, (datetime.now(UTC) - observed_at.astimezone(UTC)).total_seconds())

    def _mark_machine_accounts_unknown(
        self,
        machine_id: str,
        error_code: str,
        message: str,
        checked_at: str,
    ) -> None:
        for account in self.store.list("accounts"):
            if account.get("machine_id") == machine_id:
                self._mark_account_unknown(
                    machine_id,
                    str(account.get("account_slot") or ""),
                    error_code,
                    message,
                    checked_at,
                )

    def _mark_unreturned_accounts_unknown(
        self,
        machine_id: str,
        seen: set[str],
        checked_at: str,
    ) -> None:
        for account in self.store.list("accounts"):
            if account.get("machine_id") == machine_id and account.get("account_key") not in seen:
                self._mark_account_unknown(
                    machine_id,
                    str(account.get("account_slot") or ""),
                    "ACCOUNT_NOT_RETURNED",
                    "账号未出现在员工机最近一次账号清单中",
                    checked_at,
                )

    def _mark_account_unknown(
        self,
        machine_id: str,
        account_slot: str,
        error_code: str,
        message: str,
        checked_at: str,
    ) -> dict:
        key = self._account_key(machine_id, account_slot)
        existing = self.store.get("accounts", key) or {
            "account_key": key,
            "record_id": f"account:{key}",
            "machine_id": machine_id,
            "account_slot": account_slot,
            "name": account_slot,
            "platform": "xiaohongshu",
            "created_at": checked_at,
        }
        updated = {
            **existing,
            "health_state": "UNKNOWN",
            "data_status": "missing",
            "abnormal_reason": message,
            "recommended_action": "检查员工机 SSH 和执行服务后重新读取",
            "error_code": error_code,
            "last_error": message,
            "last_check_at": checked_at,
            "updated_at": utc_now(),
        }
        stored = self.store.put("accounts", key, updated)
        self._sync_account_alert(stored)
        return stored

    def _sync_account_alert(self, account: dict) -> None:
        key = str(account["account_key"])
        now = utc_now()
        existing = self.store.get("account_alerts", key)
        if account.get("health_state") == "READY":
            if existing and existing.get("active"):
                self.store.put(
                    "account_alerts",
                    key,
                    {**existing, "active": False, "resolved_at": now, "updated_at": now},
                )
            return
        alert = {
            "alert_id": f"account-alert:{key}",
            "account_key": key,
            "machine_id": account["machine_id"],
            "account_slot": account["account_slot"],
            "severity": account.get("health_state") or "UNKNOWN",
            "reason": (
                account.get("abnormal_reason") or account.get("last_error") or "账号状态待处理"
            ),
            "recommended_action": account.get("recommended_action") or "重新检查账号状态",
            "active": True,
            "first_seen_at": (existing or {}).get("first_seen_at") or now,
            "last_seen_at": now,
            "resolved_at": None,
            "updated_at": now,
        }
        self.store.put("account_alerts", key, alert)

    @staticmethod
    def _account_key(machine_id: str, account_slot: str) -> str:
        return f"{str(machine_id).strip().upper()}:{str(account_slot).strip()}"

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
            "task_inbox_root": str(values.get("task_inbox_root") or "").strip(),
            "task_outbox_root": str(values.get("task_outbox_root") or "").strip(),
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
            "task_inbox_root",
            "task_outbox_root",
            "allowed_versions",
            "expected_role",
            "connect_timeout_seconds",
        )
        definition = {key: machine.get(key) for key in keys}
        for key in ("task_inbox_root", "task_outbox_root"):
            definition[key] = str(definition.get(key) or "")
        return definition

    @staticmethod
    def _endpoint_key(machine: dict) -> tuple[str, int, str]:
        return (
            str(machine["ssh_host"]).lower(),
            int(machine["ssh_port"]),
            str(machine["ssh_user"]).lower(),
        )

    @staticmethod
    def _normalize_timestamp(value: object, field: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ServiceError("INVALID_TIMESTAMP", f"{field}不能为空")
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ServiceError("INVALID_TIMESTAMP", f"{field}必须是ISO时间") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC).isoformat()

    @staticmethod
    def _normalize_text_list(value: object) -> list[str]:
        if isinstance(value, str):
            values = value.replace("，", ",").split(",")
        elif isinstance(value, list):
            values = value
        else:
            values = []
        result: list[str] = []
        for item in values:
            text = str(item).strip()
            if text and text not in result:
                result.append(text)
        return result

    @classmethod
    def _normalize_persona_request(cls, request: dict) -> dict:
        machine_id = str(request.get("machine_id") or "").strip().upper()
        if machine_id in {"", "LOCAL", "MANAGER"}:
            raise ServiceError("INVALID_PERSONA", "人设卡必须绑定员工机账号，请提供员工机编号")
        account_slot = str(request.get("account_slot") or "").strip()
        if not account_slot:
            raise ServiceError("INVALID_PERSONA", "人设卡必须绑定账号槽位")
        status = str(request.get("status") or "ACTIVE").strip().upper()
        if status not in PERSONA_STATES:
            raise ServiceError("INVALID_PERSONA", "人设卡状态必须是ACTIVE或INACTIVE")
        return {
            "persona_id": str(request.get("persona_id") or "").strip().upper(),
            "machine_id": machine_id,
            "account_slot": account_slot,
            "display_name": str(request.get("display_name") or account_slot).strip(),
            "role": str(request.get("role") or "").strip(),
            "audience": str(request.get("audience") or "").strip(),
            "content_topics": cls._normalize_text_list(request.get("content_topics")),
            "tone": str(request.get("tone") or "").strip(),
            "interaction_style": str(request.get("interaction_style") or "").strip(),
            "fact_boundaries": str(request.get("fact_boundaries") or "").strip(),
            "language": str(request.get("language") or "中文").strip(),
            "knowledge": str(request.get("knowledge") or "").strip(),
            "identity_background": str(request.get("identity_background") or "").strip(),
            "professional_direction": str(request.get("professional_direction") or "").strip(),
            "reply_length": str(request.get("reply_length") or "").strip(),
            "behavior_boundaries": str(request.get("behavior_boundaries") or "").strip(),
            "status": status,
        }

    @staticmethod
    def _persona_definition(persona: dict) -> dict:
        fields = (
            "machine_id",
            "account_slot",
            "display_name",
            "role",
            "audience",
            "content_topics",
            "tone",
            "interaction_style",
            "fact_boundaries",
            "language",
            "knowledge",
            "identity_background",
            "professional_direction",
            "reply_length",
            "behavior_boundaries",
            "identity",
            "memory_namespace",
            "status",
        )
        return {field: persona.get(field) for field in fields}

    @classmethod
    def _persona_snapshot(cls, persona: dict) -> dict:
        return {
            "persona_id": str(persona.get("persona_id") or ""),
            "version": int(persona.get("version") or 0),
            "effective_at": persona.get("effective_at") or persona.get("updated_at"),
            **cls._persona_definition(persona),
        }

    def _active_persona(self, machine_id: str, account_slot: str) -> dict | None:
        from default_persona import friendly_commenter

        account_key = self._account_key(machine_id, account_slot)
        return next(
            (
                item
                for item in self.store.list("personas")
                if item.get("account_key") == account_key and item.get("status") == "ACTIVE"
            ),
            friendly_commenter(machine_id, account_slot),
        )

    @classmethod
    def _normalize_goal_request(cls, request: dict) -> dict:
        title = str(request.get("title") or "").strip()
        if not title:
            raise ServiceError("INVALID_GOAL", "运营目标名称不能为空")
        raw_targets = request.get("targets")
        if not isinstance(raw_targets, list):
            raw_targets = [
                {
                    "machine_id": request.get("machine_id") or "LOCAL",
                    "account_slot": request.get("account_slot"),
                }
            ]
        targets: list[dict[str, str]] = []
        seen_targets: set[str] = set()
        for value in raw_targets:
            if not isinstance(value, dict):
                continue
            machine_id = str(value.get("machine_id") or "LOCAL").strip().upper()
            account_slot = str(value.get("account_slot") or "").strip()
            if not account_slot:
                continue
            target_key = cls._account_key(machine_id, account_slot)
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            targets.append({"machine_id": machine_id, "account_slot": account_slot})
        if not targets:
            raise ServiceError("INVALID_GOAL", "运营目标必须至少选择一个账号")

        start_text = str(request.get("period_start") or "").strip()
        end_text = str(request.get("period_end") or "").strip()
        try:
            start_date = date.fromisoformat(start_text)
            end_date = date.fromisoformat(end_text)
        except ValueError as exc:
            raise ServiceError("INVALID_GOAL", "运营目标周期必须使用YYYY-MM-DD日期") from exc
        if end_date < start_date:
            raise ServiceError("INVALID_GOAL", "运营目标结束日期不能早于开始日期")

        raw_metrics = request.get("target_metrics") or {}
        if not isinstance(raw_metrics, dict):
            raise ServiceError("INVALID_GOAL", "运营目标指标必须是JSON对象")
        metrics: dict[str, int] = {}
        for name, value in raw_metrics.items():
            try:
                count = int(value)
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_GOAL", f"运营指标{name}必须是整数") from exc
            if count < 0:
                raise ServiceError("INVALID_GOAL", f"运营指标{name}不能小于0")
            metrics[str(name)] = count
        if not metrics and not str(request.get("desired_outcome") or "").strip():
            raise ServiceError("INVALID_GOAL", "请填写运营成果或至少一个目标指标")
        return {
            "goal_id": str(request.get("goal_id") or "").strip().upper(),
            "title": title,
            "desired_outcome": str(request.get("desired_outcome") or "").strip(),
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
            "targets": targets,
            "target_metrics": metrics,
            "content_topics": cls._normalize_text_list(request.get("content_topics")),
            "priority": str(request.get("priority") or "NORMAL").strip().upper(),
            "notes": str(request.get("notes") or "").strip(),
        }

    @staticmethod
    def _goal_definition(goal: dict) -> dict:
        fields = (
            "title",
            "desired_outcome",
            "period_start",
            "period_end",
            "targets",
            "target_metrics",
            "content_topics",
            "priority",
            "notes",
        )
        return {field: goal.get(field) for field in fields}

    @classmethod
    def _goal_snapshot(cls, goal: dict) -> dict:
        return {
            "goal_id": str(goal.get("goal_id") or ""),
            "version": int(goal.get("version") or 0),
            **cls._goal_definition(goal),
        }

    def _observed_growth_metrics(self, goal: dict, targets: list[dict]) -> Counter[str]:
        kinds = {
            "new_followers",
            "received_likes",
            "received_comments",
            "received_private_messages",
        }
        requested = kinds.intersection(goal.get("target_metrics") or {})
        if not requested or not goal.get("period_start") or not goal.get("period_end"):
            return Counter()
        operations = EmployeeOperations(
            self.store.snapshot(),
            self.list_results()["current_results"],
            self._result_metric_counts,
        )
        from datetime import timedelta

        counts: Counter[str] = Counter()
        current = date.fromisoformat(goal["period_start"])
        end = date.fromisoformat(goal["period_end"])
        while current <= end:
            for target in targets:
                values = operations.day_metrics(
                    target["machine_id"], target["account_slot"], current.isoformat()
                )
                for kind in requested:
                    confirmed = values[kind]["actual"]
                    if confirmed is None:
                        confirmed = values[kind].get("observed_new")
                    if confirmed is not None:
                        counts[kind] += confirmed
            current += timedelta(days=1)
        return counts

    def _with_goal_progress(self, goal: dict) -> dict:
        results = []
        for item in self._progress_records("results"):
            request = item.get("task_request") if isinstance(item.get("task_request"), dict) else {}
            if str(item.get("goal_id") or request.get("goal_id") or "") == goal.get("goal_id"):
                results.append(item)
        execution_targets = [
            count for item in results if (count := self._result_target_count(item)) is not None
        ]
        actual_metrics: Counter[str] = Counter()
        for item in results:
            actual_metrics.update(self._result_metric_counts(item))
        actual_metrics.update(self._observed_growth_metrics(goal, goal.get("targets") or []))
        daily_task_id = goal.get("daily_operation_task_id")
        if daily_task_id:
            daily_task = self._get_operation_task(daily_task_id)
            progress = self._with_operation_task_progress(daily_task)["progress"]
            actual_metrics = Counter(progress["actual_metrics"])
        target_metrics = dict(goal.get("target_metrics") or {})
        state_counts = Counter(
            str(item.get("state") or item.get("status") or "UNKNOWN").upper() for item in results
        )
        return {
            **goal,
            "progress": {
                "result_count": len(results),
                "target_total": (
                    sum(target_metrics.values())
                    if target_metrics
                    else sum(execution_targets)
                    if execution_targets
                    else None
                ),
                "actual_total": (
                    sum(actual_metrics.get(name, 0) for name in target_metrics)
                    if daily_task_id else sum(actual_metrics.values())
                ) if actual_metrics else None,
                "target_metrics": target_metrics,
                "actual_metrics": dict(sorted(actual_metrics.items())),
                "execution_target_total": (sum(execution_targets) if execution_targets else None),
                "state_counts": dict(sorted(state_counts.items())),
            },
        }

    @staticmethod
    def _goal_has_target(goal: dict, machine_id: str, account_slot: str) -> bool:
        target_key = ManagerService._account_key(machine_id, account_slot)
        return any(
            ManagerService._account_key(item.get("machine_id", ""), item.get("account_slot", ""))
            == target_key
            for item in goal.get("targets") or []
            if isinstance(item, dict)
        )

    def _goal_context(self, goal_id: str, machine_id: str, account_slot: str) -> tuple[dict, dict]:
        goal = self._get_goal(goal_id)
        if goal.get("state") != "ACTIVE":
            raise ServiceError("GOAL_NOT_ACTIVE", "运营目标尚未启用或已经暂停", 409)
        if not self._goal_has_target(goal, machine_id, account_slot):
            raise ServiceError("GOAL_TARGET_MISMATCH", "计划账号不在运营目标范围内", 409)
        persona = next(
            (
                item
                for item in goal.get("persona_bindings") or []
                if self._account_key(item.get("machine_id", ""), item.get("account_slot", ""))
                == self._account_key(machine_id, account_slot)
            ),
            None,
        )
        if persona is None:
            raise ServiceError("PERSONA_REQUIRED", "运营目标缺少账号人设快照", 409)
        return self._goal_snapshot(goal), dict(persona)

    @classmethod
    def _normalize_operation_task_request(cls, request: dict) -> dict:
        goal_id = str(request.get("goal_id") or "").strip().upper()
        if not goal_id:
            raise ServiceError("INVALID_OPERATION_TASK", "运营任务必须关联启用中的运营目标")
        raw_targets = request.get("targets") or []
        if not isinstance(raw_targets, list):
            raise ServiceError("INVALID_OPERATION_TASK", "运营任务账号范围必须是数组")
        targets: list[dict[str, str]] = []
        for value in raw_targets:
            if not isinstance(value, dict):
                continue
            machine_id = str(value.get("machine_id") or "LOCAL").strip().upper()
            account_slot = str(value.get("account_slot") or "").strip()
            if account_slot:
                target = {"machine_id": machine_id, "account_slot": account_slot}
                if target not in targets:
                    targets.append(target)
        return {
            "operation_task_id": str(request.get("operation_task_id") or "").strip().upper(),
            "goal_id": goal_id,
            "title": str(request.get("title") or "").strip(),
            "instructions": str(request.get("instructions") or "").strip(),
            "completion_criteria": str(request.get("completion_criteria") or "").strip(),
            "targets": targets,
            "requested_by": str(request.get("requested_by") or "WebUI用户").strip(),
            "action_inputs": dict(request.get("action_inputs") or {}),
            "plan_date": str(request.get("plan_date") or ""),
        }

    @staticmethod
    def _operation_task_definition(task: dict) -> dict:
        fields = (
            "goal_id",
            "title",
            "instructions",
            "completion_criteria",
            "targets",
            "requested_by",
            "goal_snapshot",
            "persona_bindings",
            "action_inputs",
            "plan_date",
        )
        return {field: task.get(field) for field in fields}

    def _with_operation_task_progress(self, task: dict) -> dict:
        latest_by_execution: dict[str, dict] = {}
        for item in self._progress_records("results"):
            request = item.get("task_request") if isinstance(item.get("task_request"), dict) else {}
            if str(
                item.get("operation_task_id") or request.get("operation_task_id") or ""
            ) == task.get("operation_task_id"):
                execution_id = str(item.get("remote_execution_id") or item.get("record_id") or "")
                current = latest_by_execution.get(execution_id)
                revision = int(item.get("result_revision") or item.get("sequence") or 0)
                current_revision = int(
                    (current or {}).get("result_revision") or (current or {}).get("sequence") or 0
                )
                if current is None or revision >= current_revision:
                    latest_by_execution[execution_id] = item
        results = list(latest_by_execution.values())
        actual_metrics: Counter[str] = Counter()
        for item in results:
            actual_metrics.update(self._result_metric_counts(item))
        goal_snapshot = (
            task.get("goal_snapshot") if isinstance(task.get("goal_snapshot"), dict) else {}
        )
        if task.get("daily_material"):
            actual_metrics.update(task["daily_material"].get("baseline_actual") or {})
        actual_metrics.update(
            self._observed_growth_metrics(goal_snapshot, task.get("targets") or [])
        )
        target_metrics = dict(goal_snapshot.get("target_metrics") or {})
        linked_tasks = [
            item
            for item in self._progress_records("tasks")
            if str((item.get("request") or {}).get("operation_task_id") or "")
            == task.get("operation_task_id")
        ]
        task_states = Counter(
            str(item.get("business_state") or item.get("dispatch_state") or "UNKNOWN")
            for item in linked_tasks
        )
        latest_result = max(
            results,
            key=lambda item: int(item.get("result_revision") or item.get("sequence") or 0),
            default={},
        )
        plan = task.get("plan") if isinstance(task.get("plan"), dict) else {}
        planned_actions = plan.get("planned_actions") or []
        return {
            **task,
            "result_ids": [str(item.get("record_id") or "") for item in results],
            "last_result_state": str(
                latest_result.get("state") or latest_result.get("status") or ""
            ).upper(),
            "progress": {
                "plan_action_count": sum(1 for item in planned_actions if isinstance(item, dict)),
                "schedule_count": len(task.get("schedule_ids") or []),
                "execution_task_count": len(linked_tasks),
                "result_count": len(results),
                "target_total": sum(target_metrics.values()) if target_metrics else None,
                "actual_total": (
                    sum(actual_metrics.get(name, 0) for name in target_metrics)
                    if task.get("daily_material") else sum(actual_metrics.values())
                ) if actual_metrics else None,
                "target_metrics": target_metrics,
                "actual_metrics": dict(sorted(actual_metrics.items())),
                "task_state_counts": dict(sorted(task_states.items())),
            },
        }

    def _operation_task_context(
        self,
        operation_task_id: str,
        machine_id: str,
        account_slot: str,
    ) -> tuple[dict, dict, dict]:
        task = self._get_operation_task(operation_task_id)
        if task.get("state") not in {"PLANNING", "DISPATCHING", "RUNNING"}:
            raise ServiceError(
                "OPERATION_TASK_NOT_CLAIMED",
                "运营任务需要先由经理Agent领取和规划",
                409,
            )
        goal = self._get_goal(str(task.get("goal_id") or ""))
        if goal.get("state") != "ACTIVE":
            raise ServiceError("GOAL_NOT_ACTIVE", "运营目标尚未启用或已经暂停", 409)
        target_key = self._account_key(machine_id, account_slot)
        if not any(
            self._account_key(item.get("machine_id", ""), item.get("account_slot", ""))
            == target_key
            for item in task.get("targets") or []
            if isinstance(item, dict)
        ):
            raise ServiceError("OPERATION_TASK_TARGET_MISMATCH", "执行账号不在运营任务范围内", 409)
        persona = next(
            (
                item
                for item in task.get("persona_bindings") or []
                if self._account_key(item.get("machine_id", ""), item.get("account_slot", ""))
                == target_key
            ),
            None,
        )
        if persona is None:
            raise ServiceError("PERSONA_REQUIRED", "运营任务缺少账号人设快照", 409)
        return task, dict(task.get("goal_snapshot") or {}), dict(persona)

    def _claimed_operation_task(self, operation_task_id: str, request: dict) -> dict:
        task = self._get_operation_task(operation_task_id)
        manager_agent_id = str(request.get("manager_agent_id") or "").strip()
        if not manager_agent_id or manager_agent_id != task.get("manager_agent_id"):
            raise ServiceError(
                "OPERATION_TASK_AGENT_MISMATCH",
                "经理Agent编号与任务领取记录不一致",
                409,
            )
        if task.get("state") in {"WAITING_AGENT", "COMPLETED", "BLOCKED"}:
            raise ServiceError("OPERATION_TASK_NOT_ACTIVE", "运营任务当前不能更新计划或结果", 409)
        return task

    @classmethod
    def _normalize_schedule_request(cls, request: dict) -> dict:
        required = ("schedule_id", "machine_id", "account_slot", "capability", "next_run_at")
        missing = [name for name in required if not request.get(name)]
        if missing:
            raise ServiceError("INVALID_SCHEDULE", f"调度计划缺少字段: {', '.join(missing)}")
        rule = request.get("rule") or {}
        time_window = request.get("time_window") or {}
        quota = request.get("quota") or {}
        parameters = request.get("parameters") or {}
        if not all(isinstance(item, dict) for item in (rule, time_window, quota, parameters)):
            raise ServiceError("INVALID_SCHEDULE", "规则、时间窗口、配额和参数必须是JSON对象")
        cls._validate_no_private_credentials(parameters)
        normalized_quota: dict[str, int] = {}
        for name, value in quota.items():
            try:
                count = int(value)
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_SCHEDULE", f"配额{name}必须是整数") from exc
            if count < 0:
                raise ServiceError("INVALID_SCHEDULE", f"配额{name}不能小于0")
            normalized_quota[str(name)] = count
        return {
            "schedule_id": str(request["schedule_id"]).strip(),
            "machine_id": str(request["machine_id"]).strip().upper(),
            "account_slot": str(request["account_slot"]).strip(),
            "capability": str(request["capability"]).strip(),
            "operation_task_id": str(request.get("operation_task_id") or "").strip().upper(),
            "goal_id": str(request.get("goal_id") or "").strip().upper(),
            "goal_snapshot": {},
            "persona_snapshot": {},
            "request_summary": str(request.get("request_summary") or "Agent持续运营计划").strip(),
            "rule": rule,
            "time_window": time_window,
            "quota": normalized_quota,
            "parameters": parameters,
            "next_run_at": cls._normalize_timestamp(request["next_run_at"], "next_run_at"),
        }

    @staticmethod
    def _schedule_definition(schedule: dict) -> dict:
        fields = (
            "schedule_id",
            "machine_id",
            "account_slot",
            "capability",
            "operation_task_id",
            "goal_id",
            "goal_snapshot",
            "persona_snapshot",
            "request_summary",
            "rule",
            "time_window",
            "quota",
            "parameters",
            "next_run_at",
        )
        return {field: schedule.get(field) for field in fields}

    def _attach_task_context(self, request: dict) -> dict:
        enriched = dict(request)
        schedule_id = str(enriched.get("schedule_id") or "")
        if schedule_id:
            schedule = self._get_schedule(schedule_id)
            requested_operation_task = str(enriched.get("operation_task_id") or "")
            schedule_operation_task = str(schedule.get("operation_task_id") or "")
            if requested_operation_task and requested_operation_task != schedule_operation_task:
                raise ServiceError(
                    "OPERATION_TASK_TARGET_MISMATCH",
                    "任务所属运营任务与调度计划不一致",
                    409,
                )
            requested_goal = str(enriched.get("goal_id") or "")
            schedule_goal = str(schedule.get("goal_id") or "")
            if requested_goal and requested_goal != schedule_goal:
                raise ServiceError("GOAL_TARGET_MISMATCH", "任务目标与调度计划不一致", 409)
            enriched["goal_id"] = schedule_goal
            enriched["operation_task_id"] = str(schedule.get("operation_task_id") or "")
            enriched["goal_snapshot"] = dict(schedule.get("goal_snapshot") or {})
            enriched["persona_snapshot"] = dict(schedule.get("persona_snapshot") or {})
            return self.attach_memory(enriched)
        operation_task_id = str(enriched.get("operation_task_id") or "")
        if operation_task_id:
            operation_task, goal_snapshot, persona_snapshot = self._operation_task_context(
                operation_task_id,
                str(enriched["machine_id"]),
                str(enriched["account_slot"]),
            )
            requested_goal = str(enriched.get("goal_id") or "")
            if requested_goal and requested_goal != operation_task["goal_id"]:
                raise ServiceError("GOAL_TARGET_MISMATCH", "任务目标与运营任务不一致", 409)
            enriched["goal_id"] = str(operation_task["goal_id"])
            enriched["goal_snapshot"] = goal_snapshot
            enriched["persona_snapshot"] = persona_snapshot
            if operation_task.get("daily_material"):
                spec = copy.deepcopy(operation_task["daily_material"])
                enriched["parameters"] = {**enriched["parameters"], "daily_plan": spec,
                    "operations_config": {**spec.get("operations_config", {}),
                                          "publish_rules": spec["publish_rules"]}}
            return self.attach_memory(enriched)
        goal_id = str(enriched.get("goal_id") or "")
        if goal_id:
            goal_snapshot, persona_snapshot = self._goal_context(
                goal_id,
                str(enriched["machine_id"]),
                str(enriched["account_slot"]),
            )
            enriched["goal_snapshot"] = goal_snapshot
            enriched["persona_snapshot"] = persona_snapshot
        return self.attach_memory(enriched)

    def _validate_scheduled_dispatch(self, request: dict) -> None:
        schedule_id = str(request.get("schedule_id") or "").strip()
        if not schedule_id:
            return
        schedule = self._get_schedule(schedule_id)
        if schedule.get("state") != "ACTIVE":
            raise ServiceError("SCHEDULE_PAUSED", "Agent调度计划当前已暂停", 409)
        if schedule.get("operation_task_id"):
            self._operation_task_context(
                str(schedule["operation_task_id"]),
                str(schedule["machine_id"]),
                str(schedule["account_slot"]),
            )
        elif schedule.get("goal_id"):
            self._goal_context(
                str(schedule["goal_id"]),
                str(schedule["machine_id"]),
                str(schedule["account_slot"]),
            )
        expected = (
            ("machine_id", "执行机"),
            ("account_slot", "账号槽位"),
            ("capability", "能力"),
            ("operation_task_id", "运营任务"),
            ("goal_id", "运营目标"),
        )
        for field, label in expected:
            if str(schedule.get(field) or "") != str(request.get(field) or ""):
                raise ServiceError("SCHEDULE_TARGET_MISMATCH", f"任务{label}与调度计划不一致", 409)
        unresolved = [
            task
            for task in self.store.list("tasks")
            if task.get("task_id") != request["task_id"]
            and (task.get("request") or {}).get("schedule_id") == schedule_id
            and (
                task.get("reconciliation_required")
                or str(task.get("business_state") or "").upper() == "RESULT_UNKNOWN"
            )
        ]
        if unresolved:
            raise ServiceError(
                "SCHEDULE_RECONCILIATION_REQUIRED",
                "上一次调度结果需要先对账，当前任务尚未派发",
                409,
            )

    @classmethod
    def _normalize_report_request(cls, request: dict) -> dict:
        report_type = str(request.get("report_type") or "daily").strip().lower()
        if report_type not in {"daily", "weekly"}:
            raise ServiceError("INVALID_REPORT", "经理机运营报告仅支持daily或weekly")
        start_at = cls._normalize_timestamp(request.get("start_at"), "start_at")
        end_at = cls._normalize_timestamp(request.get("end_at"), "end_at")
        if datetime.fromisoformat(start_at) > datetime.fromisoformat(end_at):
            raise ServiceError("INVALID_REPORT", "报告开始时间不能晚于结束时间")
        machine_id = str(request.get("machine_id") or "").strip().upper()
        return {
            "report_type": report_type,
            "machine_id": machine_id,
            "account_slot": str(request.get("account_slot") or "").strip(),
            "start_at": start_at,
            "end_at": end_at,
            "source_type": "agent",
        }

    @staticmethod
    def _result_timestamp(result: dict) -> datetime | None:
        value = (
            result.get("recorded_at")
            or result.get("collected_at")
            or result.get("updated_at")
            or result.get("created_at")
        )
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _result_account(result: dict) -> str:
        request = result.get("task_request") if isinstance(result.get("task_request"), dict) else {}
        return str(result.get("account_slot") or request.get("account_slot") or "")

    @staticmethod
    def _result_action_kind(result: dict) -> str:
        request = result.get("task_request") if isinstance(result.get("task_request"), dict) else {}
        capability = str(result.get("capability") or request.get("capability") or "").lower()
        if capability == "send-private-messages":
            return "private_reply" if (request.get("parameters") or {}).get(
                "acceptance_action"
            ) == "private_reply" else "private_message"
        if capability == "reply-inbox" and (request.get("parameters") or {}).get(
            "acceptance_action"
        ) == "private_reply":
            return "private_reply"
        if "reply" in capability:
            return "reply"
        if "comment" in capability:
            return "comment"
        if "like" in capability:
            return "like"
        if "publish" in capability or "article" in capability:
            return "publish"
        if any(value in capability for value in ("feed", "search", "browse")):
            return "browse"
        return "other"

    @classmethod
    def _result_target_count(cls, result: dict) -> int | None:
        request = result.get("task_request") if isinstance(result.get("task_request"), dict) else {}
        parameters = (
            request.get("parameters") if isinstance(request.get("parameters"), dict) else {}
        )
        capability = str(result.get("capability") or request.get("capability") or "").lower()
        if capability == "home-engagement" and any(
            key in parameters for key in ("browse_count", "like_count")
        ):
            try:
                return max(0, int(parameters.get("browse_count") or 0)) + max(
                    0, int(parameters.get("like_count") or 0)
                )
            except (TypeError, ValueError):
                pass
        for value in (
            result.get("target_count"),
            result.get("expected_count"),
            request.get("target_count"),
            parameters.get("target_count"),
            parameters.get("expected_count"),
            parameters.get("count"),
            parameters.get("limit"),
        ):
            if value is None or value == "":
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count >= 0:
                return count
        targets = parameters.get("target_ids")
        if isinstance(targets, list):
            return len(targets)
        if request.get("target_id"):
            return 1
        write_kinds = {"like", "comment", "reply", "publish"}
        return 1 if cls._result_action_kind(result) in write_kinds else None

    @classmethod
    def _result_actual_count(cls, result: dict) -> int | None:
        for value in (result.get("actual_count"), result.get("completed_count")):
            if value is None or value == "":
                continue
            try:
                count = int(value)
            except (TypeError, ValueError):
                continue
            if count >= 0:
                return count
        item_status = str(
            result.get("item_results_status")
            or ("observed" if "item_results" in result else "missing")
        )
        if item_status == "observed" and isinstance(result.get("item_results"), list):
            request = (
                result.get("task_request") if isinstance(result.get("task_request"), dict) else {}
            )
            capability = str(result.get("capability") or request.get("capability") or "").lower()
            if capability == "home-engagement":
                completed = 0
                for item in result["item_results"]:
                    if not isinstance(item, dict):
                        continue
                    browse = item.get("browse") if isinstance(item.get("browse"), dict) else {}
                    like = item.get("like") if isinstance(item.get("like"), dict) else {}
                    completed += str(browse.get("status") or "").lower() == "success"
                    completed += (
                        like.get("success") is True
                        or str(like.get("status") or "").lower() == "success"
                    )
                return completed
            if any(
                not isinstance(item, dict) or not (item.get("state") or item.get("status"))
                for item in result["item_results"]
            ):
                return None
            return sum(
                str(item.get("state") or item.get("status") or "").upper()
                in {"SUCCESS", "COMPLETED"}
                for item in result["item_results"]
                if isinstance(item, dict)
            )
        if str(result.get("state") or result.get("status") or "").upper() == "SUCCESS":
            return 1 if cls._result_target_count(result) == 1 else None
        return None

    @classmethod
    def _result_metric_counts(cls, result: dict) -> Counter[str]:
        from compound_receipt import confirmed_action_counts

        compound_counts = confirmed_action_counts(result)
        if compound_counts is not None:
            return compound_counts
        request = result.get("task_request") if isinstance(result.get("task_request"), dict) else {}
        capability = str(result.get("capability") or request.get("capability") or "").lower()
        if capability == "send-private-messages":
            if result.get("state") not in {"SUCCESS", "PARTIAL_SUCCESS"}:
                if result.get("state") in {"BLOCKED", "FAILED", "CANCELLED"} and (
                    cls._result_actual_count(result) == 0
                ):
                    return Counter({cls._result_action_kind(result): 0})
                return Counter()
            from operations_acceptance import confirmed_counts

            request = {**request, "parameters": {**(request.get("parameters") or {}),
                       "acceptance_action": cls._result_action_kind(result)}}
            return confirmed_counts(result, request)
        if capability in {
            "collect-operations-metrics",
            "collect-note-comments",
            "collect-private-messages",
        }:
            return Counter()
        if capability in {"fill-publish", "fill-publish-video", "long-article"}:
            body = result.get("business_result") or {}
            from publish_workflow import VERIFIED_PUBLISH_EVIDENCE

            return (
                Counter({"publish": 1})
                if (
                    result.get("state") == "SUCCESS"
                    and body.get("verified")
                    and body.get("evidence") in VERIFIED_PUBLISH_EVIDENCE
                )
                else Counter()
            )
        if capability == "follow-user":
            body = result.get("business_result") or {}
            return (
                Counter({"friend_request": int(body.get("changed") is True)})
                if body.get("success") is True
                else Counter()
            )
        item_status = str(
            result.get("item_results_status")
            or ("observed" if "item_results" in result else "missing")
        )
        item_results = result.get("item_results")
        if (
            capability == "home-engagement"
            and item_status == "observed"
            and isinstance(item_results, list)
        ):
            browsed = 0
            liked = 0
            for item in item_results:
                if not isinstance(item, dict):
                    continue
                browse = item.get("browse") if isinstance(item.get("browse"), dict) else {}
                flat_success = (
                    str(item.get("status") or "").upper() not in {
                        "FAILED", "BLOCKED", "SKIPPED", "RESULT_UNKNOWN",
                        "RECONCILIATION_REQUIRED",
                    }
                    and (item.get("success") is True
                         or str(item.get("status") or "").upper() == "SUCCESS")
                )
                if (str(browse.get("status") or "").lower() == "success"
                        or (flat_success and (
                            item.get("browsed") is True
                            or item.get("browse_success") is True
                            or str(item.get("browse_status") or "").lower() == "success"
                        ))):
                    browsed += 1
                like = item.get("like") if isinstance(item.get("like"), dict) else {}
                if (
                    like.get("success") is True
                    or str(like.get("status") or "").lower() == "success"
                    or (flat_success and item.get("liked") is True
                        and item.get("platform_readback") is True
                        and item.get("changed") is not False)
                ):
                    liked += 1
            counts: Counter[str] = Counter()
            if browsed:
                counts["browse"] = browsed
            if liked:
                counts["like"] = liked
            return counts

        count = cls._result_actual_count(result)
        if count is None:
            return Counter()
        return Counter({cls._result_action_kind(result): count})

    def _create_operation_report(
        self,
        definition: dict,
        *,
        root_report_id: str = "",
        previous_report_id: str = "",
        revision_number: int = 1,
        revision_reason: str = "",
    ) -> dict:
        start = datetime.fromisoformat(definition["start_at"])
        end = datetime.fromisoformat(definition["end_at"])
        results: list[dict] = []
        for result in self.store.list("results"):
            timestamp = self._result_timestamp(result)
            if timestamp is None or timestamp < start or timestamp > end:
                continue
            if definition["machine_id"] and result.get("machine_id") != definition["machine_id"]:
                continue
            if (
                definition["account_slot"]
                and self._result_account(result) != definition["account_slot"]
            ):
                continue
            results.append(result)
        summary = self._summarize_results(results)
        terminal = [
            item
            for item in results
            if str(item.get("state") or item.get("status") or "").upper() in REMOTE_TERMINAL_STATES
        ]
        successful = sum(
            str(item.get("state") or item.get("status") or "").upper() == "SUCCESS"
            for item in terminal
        )
        action_summary: dict[str, dict[str, int | None]] = {}
        for kind in (
            "browse", "like", "comment", "reply", "publish", "private_message", "private_reply"
        ):
            selected = [item for item in results if self._result_action_kind(item) == kind]
            targets = [
                count for item in selected if (count := self._result_target_count(item)) is not None
            ]
            actuals = [
                count for item in selected if (count := self._result_actual_count(item)) is not None
            ]
            action_summary[kind] = {
                "target": sum(targets) if targets else None,
                "actual": sum(actuals) if actuals else None,
            }
        all_targets = [
            count for item in results if (count := self._result_target_count(item)) is not None
        ]
        all_actuals = [
            count for item in results if (count := self._result_actual_count(item)) is not None
        ]
        report_id = uuid.uuid4().hex
        root_id = root_report_id or report_id
        data_gaps = list(summary.get("data_gaps") or [])
        report = {
            "report_id": report_id,
            "root_report_id": root_id,
            "previous_report_id": previous_report_id,
            "revision_number": revision_number,
            "revision_reason": revision_reason,
            "definition": dict(definition),
            "source_type": "agent",
            "data_status": "missing" if not results else ("mixed" if data_gaps else "observed"),
            "missing_reason": "筛选范围内没有已回收结果" if not results else "",
            "content": {
                "result_count": len(results),
                "state_counts": summary.get("state_counts") or {},
                "target_total": sum(all_targets) if all_targets else None,
                "actual_total": sum(all_actuals) if all_actuals else None,
                "success_rate": round(successful / len(terminal), 4) if terminal else None,
                "exception_rate": (
                    round((len(terminal) - successful) / len(terminal), 4) if terminal else None
                ),
                "actions": action_summary,
                "data_gap_count": len(data_gaps),
            },
            "data_gaps": data_gaps,
            "source_result_ids": [
                str(item.get("record_id") or item.get("task_id") or "") for item in results
            ],
            "created_at": utc_now(),
        }
        return self.store.put("operation_reports", report_id, report)

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
            "operation": str(request.get("operation") or "").strip() or None,
            "platform": str(request.get("platform") or "xiaohongshu").strip(),
            "request_summary": str(request.get("request_summary") or "经理机下发任务").strip(),
            "source_type": str(request.get("source_type") or "manager").strip(),
            "target_type": str(request.get("target_type") or "").strip(),
            "target_id": str(request.get("target_id") or "").strip(),
            "schedule_id": str(request.get("schedule_id") or "").strip(),
            "operation_task_id": str(request.get("operation_task_id") or "").strip().upper(),
            "goal_id": str(request.get("goal_id") or "").strip().upper(),
            "goal_snapshot": {},
            "persona_snapshot": {},
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
        manager_agent_id = "MANAGER-AGENT-01"
        operation_task_id = str(request.get("operation_task_id") or "")
        if operation_task_id:
            operation_task = self.store.get("operation_tasks", operation_task_id) or {}
            manager_agent_id = str(operation_task.get("manager_agent_id") or manager_agent_id)
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
            "schedule_id": request["schedule_id"],
            "operation_task_id": request["operation_task_id"],
            "goal_id": request["goal_id"],
            "goal_snapshot": request["goal_snapshot"],
            "persona_snapshot": request["persona_snapshot"],
            "manager_id": machine["manager_id"],
            "manager_agent_id": manager_agent_id,
            "result_transport": {"type": "sftp_outbox", "collector": "manager_pull"},
            "function_id": request["function_id"],
            "allowed_function_versions": request["allowed_function_versions"],
            "parameters": request["parameters"],
            "authorization": request["authorization"],
            "allowed_versions": request["allowed_versions"] or machine["allowed_versions"],
            "write_intent": request["write_intent"],
            "timeout_seconds": request["timeout_seconds"],
        }

    def _uses_sftp_task_exchange(self, machine: dict) -> bool:
        inbox_root = str(
            machine.get("task_inbox_root") or machine.get("observed_task_inbox_root") or ""
        ).strip()
        outbox_root = str(
            machine.get("task_outbox_root") or machine.get("observed_task_outbox_root") or ""
        ).strip()
        return bool(
            inbox_root
            and outbox_root
            and callable(getattr(self.transport, "sftp_upload_atomic", None))
        )

    def _dispatch_task_via_exchange(
        self,
        task: dict,
        machine: dict,
        envelope: dict,
    ) -> dict:
        task_id = str(task["task_id"])
        file_name = f"{self._safe_path_component(task_id)}.json"
        local_path = self._write_task_package(machine["machine_id"], file_name, envelope)
        inbox_root = str(
            machine.get("task_inbox_root") or machine.get("observed_task_inbox_root") or ""
        )
        remote_path = self._join_remote_path(
            self._join_remote_path(inbox_root, "pending"),
            file_name,
        )
        already_transferred = task.get("dispatch_state") == "TRANSFERRED" and bool(
            task.get("task_package_remote_path")
        )
        if not already_transferred:
            try:
                self.transport.sftp_upload_atomic(
                    machine,
                    local_path,
                    remote_path,
                    timeout_seconds=60,
                )
            except ManagerTransportError as exc:
                unknown = exc.result_unknown
                updated = self._update_task(
                    task_id,
                    dispatch_state="TRANSFER_UNKNOWN" if unknown else "PENDING_DISPATCH",
                    connectivity="CONTACT_LOST" if unknown else "OFFLINE",
                    reconciliation_required=unknown,
                    error_code=exc.code,
                    message=(
                        "任务包传输结果未知，恢复连接后必须先检查员工机 inbox"
                        if unknown
                        else "任务包尚未传到员工机"
                    ),
                    task_package_local_path=str(local_path),
                    task_package_remote_path=remote_path,
                )
                return {"success": False, "status": updated["dispatch_state"], "task": updated}

            self._update_task(
                task_id,
                dispatch_state="TRANSFERRED",
                connectivity="ONLINE",
                reconciliation_required=False,
                error_code="",
                message="任务包已传到员工机 inbox，正在通过 SSH 唤醒员工 Agent",
                task_package_local_path=str(local_path),
                task_package_remote_path=remote_path,
                transferred_at=utc_now(),
            )
        try:
            acknowledgement = self.transport.invoke(
                machine,
                "wake-task",
                {"task_id": task_id, "file_name": file_name},
                timeout_seconds=15,
            )
        except ManagerTransportError as exc:
            updated = self._update_task(
                task_id,
                dispatch_state="WAKE_UNKNOWN" if exc.result_unknown else "TRANSFERRED",
                connectivity="CONTACT_LOST" if exc.result_unknown else "OFFLINE",
                reconciliation_required=exc.result_unknown,
                error_code=exc.code,
                message=(
                    "任务包已到达，SSH 唤醒确认中断，必须先对账"
                    if exc.result_unknown
                    else "任务包已到达，员工机尚未确认领取"
                ),
            )
            return {"success": False, "status": updated["dispatch_state"], "task": updated}

        remote_error = self._remote_error(acknowledgement)
        if remote_error:
            error_code, message = remote_error
            if error_code == "INTERNAL_ERROR":
                self._update_task(
                    task_id,
                    dispatch_state="WAKE_UNKNOWN",
                    connectivity="ONLINE",
                    reconciliation_required=True,
                    error_code=error_code,
                    message="唤醒确认异常，正在按原任务编号核对员工执行状态",
                )
                return self.reconcile_task(task_id)
            updated = self._update_task(
                task_id,
                dispatch_state="BLOCKED",
                connectivity="ONLINE",
                reconciliation_required=False,
                error_code=error_code,
                message=message,
            )
            return {"success": False, "status": "BLOCKED", "task": updated}

        self._record_remote_agent_messages(acknowledgement)
        execution = self._extract_execution(acknowledgement)
        updated = self._update_task_from_execution(task_id, execution, connectivity="ONLINE")
        updated = self._update_task(
            task_id,
            dispatch_state="ACKNOWLEDGED",
            business_state="",
            reconciliation_required=False,
            error_code="",
            message="员工机已领取任务并启动后台执行",
            acknowledged_at=utc_now(),
        )
        return {
            "success": True,
            "status": "TASK_ACK",
            "task": updated,
            "execution": execution,
            "acknowledgement": acknowledgement.get("acknowledgement") or {},
        }

    def _write_task_package(
        self,
        machine_id: str,
        file_name: str,
        envelope: dict,
    ) -> Path:
        directory = self.store.root / "task-outbox" / self._safe_path_component(machine_id)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / file_name
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        return path

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
        if transfer_type not in {"task_package", "function_package", "config", "repair_package"}:
            raise ServiceError("INVALID_TRANSFER", "传输类型必须是任务包、功能包、修复包或配置")
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
        return self._with_machine_freshness(machine)

    def _get_task(self, task_id: str) -> dict:
        task = self.store.get("tasks", task_id)
        if task is None:
            raise ServiceError("TASK_NOT_FOUND", "经理机任务不存在", 404)
        return task

    def _get_schedule(self, schedule_id: str) -> dict:
        schedule = self.store.get("schedules", str(schedule_id).strip())
        if schedule is None:
            raise ServiceError("SCHEDULE_NOT_FOUND", "Agent调度计划不存在", 404)
        return schedule

    def _get_persona(self, persona_id: str) -> dict:
        persona = self.store.get("personas", str(persona_id).strip().upper())
        if persona is None:
            raise ServiceError("PERSONA_NOT_FOUND", "账号人设卡不存在", 404)
        return persona

    def _get_goal(self, goal_id: str) -> dict:
        goal = self.store.get("goals", str(goal_id).strip().upper())
        if goal is None:
            raise ServiceError("GOAL_NOT_FOUND", "运营目标不存在", 404)
        return goal

    def _get_operation_task(self, operation_task_id: str) -> dict:
        identifier = str(operation_task_id).strip()
        task = self.store.get("operation_tasks", identifier)
        if task is None:
            task = self.store.get("operation_tasks", identifier.upper())
        if task is None:
            raise ServiceError("OPERATION_TASK_NOT_FOUND", "运营任务不存在", 404)
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
        schedule_id = str((updated.get("request") or {}).get("schedule_id") or "")
        terminal_state = str(updated.get("business_state") or "").upper()
        if schedule_id and terminal_state in REMOTE_TERMINAL_STATES:
            self.record_schedule_run(
                schedule_id,
                {
                    "task_id": task_id,
                    "state": terminal_state,
                    "result_summary": updated.get("result_summary") or updated.get("message") or "",
                    "recorded_at": updated.get("updated_at"),
                },
            )
        operation_task_id = str((updated.get("request") or {}).get("operation_task_id") or "")
        operation_task = (
            self.store.get("operation_tasks", operation_task_id) if operation_task_id else None
        )
        if operation_task and operation_task.get("state") not in {"COMPLETED", "BLOCKED"}:
            if updated.get("reconciliation_required") or terminal_state == "RESULT_UNKNOWN":
                operation_state = "RECONCILIATION_REQUIRED"
            elif str(updated.get("dispatch_state") or "") == "PENDING_DISPATCH":
                operation_state = "DISPATCHING"
            else:
                operation_state = "RUNNING"
            self._update_operation_task(operation_task_id, state=operation_state)
        return updated

    def _update_schedule(self, schedule_id: str, **values) -> dict:
        values["updated_at"] = utc_now()
        updated = self.store.update(
            "schedules",
            str(schedule_id).strip(),
            lambda current: {**current, **values},
        )
        if updated is None:
            raise ServiceError("SCHEDULE_NOT_FOUND", "Agent调度计划不存在", 404)
        return updated

    def _update_goal(self, goal_id: str, **values) -> dict:
        values["updated_at"] = utc_now()
        updated = self.store.update(
            "goals",
            str(goal_id).strip().upper(),
            lambda current: {**current, **values},
        )
        if updated is None:
            raise ServiceError("GOAL_NOT_FOUND", "运营目标不存在", 404)
        return updated

    def _update_operation_task(self, operation_task_id: str, **values) -> dict:
        values["updated_at"] = utc_now()
        identifier = str(operation_task_id).strip()
        if self.store.get("operation_tasks", identifier) is None:
            identifier = identifier.upper()
        updated = self.store.update(
            "operation_tasks",
            identifier,
            lambda current: {**current, **values},
        )
        if updated is None:
            raise ServiceError("OPERATION_TASK_NOT_FOUND", "运营任务不存在", 404)
        return updated

    def _link_operation_schedule(self, operation_task_id: str, schedule_id: str) -> dict:
        task = self._get_operation_task(operation_task_id)
        schedule_ids = list(task.get("schedule_ids") or [])
        if schedule_id not in schedule_ids:
            schedule_ids.append(schedule_id)
        return self._update_operation_task(
            operation_task_id,
            schedule_ids=schedule_ids,
            state="RUNNING",
        )

    def _link_operation_execution_task(self, operation_task_id: str, task_id: str) -> dict:
        task = self._get_operation_task(operation_task_id)
        task_ids = list(task.get("execution_task_ids") or [])
        if task_id not in task_ids:
            task_ids.append(task_id)
        return self._update_operation_task(
            operation_task_id,
            execution_task_ids=task_ids,
            state="DISPATCHING",
        )

    def _record_operation_result(self, result: dict) -> None:
        operation_task_id = str(result.get("operation_task_id") or "")
        if not operation_task_id:
            return
        task = self.store.get("operation_tasks", operation_task_id)
        if task is None or task.get("state") in {"COMPLETED", "BLOCKED"}:
            return
        result_ids = list(task.get("result_ids") or [])
        record_id = str(result.get("record_id") or "")
        if record_id and record_id not in result_ids:
            result_ids.append(record_id)
        result_state = str(result.get("state") or result.get("status") or "UNKNOWN").upper()
        next_state = "RECONCILIATION_REQUIRED" if result_state == "RESULT_UNKNOWN" else "RUNNING"
        self._update_operation_task(
            operation_task_id,
            result_ids=result_ids,
            last_result_state=result_state,
            state=next_state,
        )

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
