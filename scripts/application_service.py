"""Shared read-only application services for CLI, WebUI, and agents."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from account_autostart import (
    account_autostart_status,
    disable_account_autostart,
    enable_account_autostart,
)
from account_doctor import diagnose_accounts
from account_identity import load_identity_record, record_current_identity
from account_manager import (
    AccountConfig,
    add_account,
    discover_chrome_profiles,
    import_existing_profile,
    list_accounts,
    load_account,
    public_config,
)
from account_pairing import create_pairing_session, get_pairing_status
from account_runtime import evaluate_profile_connection
from approval_service import ApprovalService
from bridge_lifecycle import (
    get_bridge_lifecycle,
    restart_bridge,
    start_bridge,
    stop_bridge,
)
from business_runner import BusinessRunner
from capability_registry import (
    CapabilityPolicy,
    get_operation_policy,
    list_capability_policies,
)
from diagnostic_export import export_diagnostic_report
from product_store import ProductStore
from quota_service import QuotaService
from service_errors import ServiceError
from task_service import TaskService
from xhs.bridge import BridgePage
from xhs.login import get_current_user_identity

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXTENSION_SOURCE = PROJECT_ROOT / "extension"
PRODUCT_VERSION = "0.1.0-v1"


class ApplicationService:
    """Read-only product boundary shared by CLI and the local HTTP API."""

    def __init__(
        self,
        *,
        account_lister: Callable[[], list[AccountConfig]] = list_accounts,
        account_loader: Callable[..., AccountConfig] = load_account,
        diagnostic: Callable[..., dict] = diagnose_accounts,
        page_factory: Callable[..., BridgePage] = BridgePage,
        identity_loader: Callable[[str], dict | None] = load_identity_record,
        identity_observer: Callable[[object], dict] = get_current_user_identity,
        account_creator: Callable[..., AccountConfig] = add_account,
        profile_importer: Callable[..., AccountConfig] = import_existing_profile,
        profile_discoverer: Callable[..., list[dict]] = discover_chrome_profiles,
        identity_recorder: Callable[..., dict] = record_current_identity,
        product_store: ProductStore | None = None,
        extension_source: str | Path = DEFAULT_EXTENSION_SOURCE,
        business_runner: BusinessRunner | None = None,
        bridge_status_reader: Callable[..., dict] = get_bridge_lifecycle,
        bridge_starter: Callable[..., dict] = start_bridge,
        bridge_stopper: Callable[..., dict] = stop_bridge,
        bridge_restarter: Callable[..., dict] = restart_bridge,
        diagnostic_exporter: Callable[..., Path] = export_diagnostic_report,
        autostart_reader: Callable[[str], dict] = account_autostart_status,
        autostart_enabler: Callable[[str], dict] = enable_account_autostart,
        autostart_disabler: Callable[[str], dict] = disable_account_autostart,
    ) -> None:
        self._account_lister = account_lister
        self._account_loader = account_loader
        self._diagnostic = diagnostic
        self._page_factory = page_factory
        self._identity_loader = identity_loader
        self._identity_observer = identity_observer
        self._account_creator = account_creator
        self._profile_importer = profile_importer
        self._profile_discoverer = profile_discoverer
        self._identity_recorder = identity_recorder
        self._extension_source = Path(extension_source).resolve()
        self._bridge_status_reader = bridge_status_reader
        self._bridge_starter = bridge_starter
        self._bridge_stopper = bridge_stopper
        self._bridge_restarter = bridge_restarter
        self._diagnostic_exporter = diagnostic_exporter
        self._autostart_reader = autostart_reader
        self._autostart_enabler = autostart_enabler
        self._autostart_disabler = autostart_disabler
        self._store = product_store or ProductStore()
        self.tasks = TaskService(self._store)
        self.approvals = ApprovalService(self._store)
        self.quota = QuotaService(self._store)
        self._runner_is_managed = business_runner is None
        self.runner = business_runner or BusinessRunner(
            max_concurrency=int(self._store.get_setting("global_concurrency", 3))
        )

    def health(self) -> dict:
        return {
            "success": True,
            "service": "auto-xhs-local",
            "status": "ok",
            "api_version": "v1",
            "product_version": PRODUCT_VERSION,
        }

    def list_capabilities(self) -> dict:
        capabilities = [item.to_dict() for item in list_capability_policies()]
        return {
            "success": True,
            "capabilities": capabilities,
            "summary": {
                "total": len(capabilities),
                "enabled_in_v1": sum(bool(item["enabled_in_v1"]) for item in capabilities),
                "schedulable": sum(
                    bool(item["supports_scheduling"]) for item in capabilities
                ),
            },
        }

    def require_enabled_capability(
        self,
        command: str,
        *,
        operation: str | None = None,
    ) -> CapabilityPolicy:
        """Resolve product policy and reject capabilities disabled in V1."""

        try:
            policy = get_operation_policy(command, operation)
        except KeyError as exc:
            raise ServiceError("CAPABILITY_NOT_FOUND", str(exc), 404) from exc
        if not policy.enabled_in_v1:
            raise ServiceError(
                "CAPABILITY_DISABLED",
                f"{command} 不属于当前 V1 开放能力",
                409,
            )
        return policy

    def list_accounts(self) -> dict:
        return {
            "success": True,
            "accounts": [public_config(config) for config in self._account_lister()],
        }

    def discover_profiles(self, user_data_dir: str | None = None) -> dict:
        try:
            profiles = self._profile_discoverer(user_data_dir)
        except (FileNotFoundError, ValueError) as exc:
            raise ServiceError("PROFILE_DISCOVERY_FAILED", str(exc), 400) from exc
        return {"success": True, "profiles": profiles}

    def create_account_slot(
        self,
        *,
        name: str,
        confirmed: bool,
        bridge_port: int | None = None,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "创建账号槽位需要明确确认", 409)
        self.require_enabled_capability("account-add")
        self._ensure_slot_capacity()
        try:
            config = self._account_creator(
                name,
                bridge_port=bridge_port,
                extension_source=self._extension_source,
            )
        except (FileExistsError, FileNotFoundError, ValueError) as exc:
            raise ServiceError("ACCOUNT_CREATE_FAILED", str(exc), 409) from exc
        return {
            "success": True,
            "account": public_config(config),
            "next_action": "手动打开新槽位的 Chrome Profile，再完成扩展配对",
        }

    def import_account_slot(
        self,
        *,
        name: str,
        user_data_dir: str,
        profile_directory: str,
        confirmed: bool,
        bridge_port: int | None = None,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "绑定 Chrome Profile 需要明确确认", 409)
        self.require_enabled_capability("account-import")
        self._ensure_slot_capacity()
        try:
            config = self._profile_importer(
                name,
                user_data_dir=user_data_dir,
                profile_directory=profile_directory,
                bridge_port=bridge_port,
                extension_source=self._extension_source,
            )
        except (FileExistsError, FileNotFoundError, ValueError) as exc:
            raise ServiceError("ACCOUNT_IMPORT_FAILED", str(exc), 409) from exc
        return {
            "success": True,
            "account": public_config(config),
            "next_action": "手动打开该 Chrome Profile，再完成扩展配对",
        }

    def begin_account_pairing(
        self,
        account: str,
        *,
        confirmed: bool,
        ttl_seconds: int = 300,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "创建配对请求需要明确确认", 409)
        self.require_enabled_capability("account-pair-begin")
        config = self._load_account(account)
        try:
            pairing = create_pairing_session(config, ttl_seconds=ttl_seconds)
        except (RuntimeError, ValueError) as exc:
            raise ServiceError("PAIRING_FAILED", str(exc), 409) from exc
        return {"success": True, "pairing": pairing}

    def account_pairing_status(self, account: str) -> dict:
        try:
            status = get_pairing_status(account)
        except (FileNotFoundError, ValueError) as exc:
            raise ServiceError("ACCOUNT_NOT_FOUND", str(exc), 404) from exc
        return {"success": True, "pairing": status}

    def check_account_identity(self, account: str) -> dict:
        self.require_enabled_capability("account-identity", operation="check")
        config = self._load_account(account)
        page = self._page_factory(config.bridge_url, account=config.name)
        try:
            identity = self._identity_observer(page)
        except Exception as exc:
            raise ServiceError("IDENTITY_CHECK_FAILED", str(exc), 409) from exc
        if not identity.get("logged_in"):
            raise ServiceError("LOGIN_REQUIRED", "当前 Profile 尚未登录小红书", 409)
        checks = dict(self._store.get_setting("runtime_identity_checks", {}))
        checks[account] = {
            "user_id": str(identity.get("user_id") or ""),
            "nickname": str(identity.get("nickname") or ""),
            "observed_at": str(identity.get("observed_at") or ""),
        }
        self._store.set_setting("runtime_identity_checks", checks)
        return {"success": True, "account": account, "identity": identity}

    def record_account_identity(self, account: str, *, confirmed: bool, label: str = "") -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "记录当前 UID 需要明确确认", 409)
        self.require_enabled_capability("account-identity", operation="record")
        observed = self.check_account_identity(account)["identity"]
        try:
            record = self._identity_recorder(
                account,
                observed,
                source="webui",
                label=label,
            )
        except ValueError as exc:
            raise ServiceError("IDENTITY_RECORD_FAILED", str(exc), 409) from exc
        return {"success": True, "identity": record}

    def system_status(self) -> dict:
        limits = self._store.get_setting(
            "l1_limits",
            {"hourly": 20, "daily": 100, "dedup_minutes": 10, "failure_threshold": 3},
        )
        tasks = self.tasks.list()
        drafts = self._store.list("drafts")
        records = self._store.list("events")
        return {
            "success": True,
            "global_paused": bool(self._store.get_setting("global_paused", False)),
            "global_concurrency": int(self._store.get_setting("global_concurrency", 3)),
            "l1_limits": limits,
            "product_version": PRODUCT_VERSION,
            "storage_root": str(self._store.root),
            "summary": {
                "tasks_total": len(tasks),
                "tasks_waiting": sum(item["state"] == "QUEUED" for item in tasks),
                "drafts_waiting": sum(item.get("status") == "DRAFT" for item in drafts),
                "recent_failures": sum(
                    item.get("state") in {"FAILED", "RESULT_UNKNOWN", "BLOCKED"}
                    for item in records[-20:]
                ),
            },
        }

    def set_global_pause(self, paused: bool) -> dict:
        settings = self._store.set_setting("global_paused", bool(paused))
        return {"success": True, "global_paused": settings["global_paused"]}

    def update_system_settings(
        self,
        *,
        confirmed: bool,
        global_concurrency: int,
        l1_limits: dict,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "修改运行设置需要明确确认", 409)
        concurrency = int(global_concurrency)
        if concurrency not in {1, 2, 3}:
            raise ServiceError("INVALID_REQUEST", "全局并发必须为 1、2 或 3")
        required = ("hourly", "daily", "dedup_minutes", "failure_threshold")
        try:
            limits = {key: int(l1_limits[key]) for key in required}
        except (KeyError, TypeError, ValueError) as exc:
            raise ServiceError("INVALID_REQUEST", "L1 配额设置不完整") from exc
        if any(value <= 0 for value in limits.values()) or limits["daily"] < limits["hourly"]:
            raise ServiceError("INVALID_REQUEST", "L1 配额必须为正数，且每日限额不小于每小时限额")

        def update(state: dict) -> None:
            state["settings"]["global_concurrency"] = concurrency
            state["settings"]["l1_limits"] = limits

        self._store.mutate(update)
        if self._runner_is_managed:
            self.runner = BusinessRunner(max_concurrency=concurrency)
        return self.system_status()

    def get_account_autostart(self, account: str) -> dict:
        self._load_account(account)
        try:
            status = self._autostart_reader(account)
        except RuntimeError as exc:
            raise ServiceError("AUTOSTART_STATUS_FAILED", str(exc), 409) from exc
        return {"success": True, "autostart": status}

    def set_account_autostart(self, account: str, *, enabled: bool, confirmed: bool) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "修改 Bridge 自启动需要明确确认", 409)
        command = "account-autostart-enable" if enabled else "account-autostart-disable"
        self.require_enabled_capability(command)
        self._load_account(account)
        try:
            status = (
                self._autostart_enabler(account)
                if enabled
                else self._autostart_disabler(account)
            )
        except RuntimeError as exc:
            raise ServiceError("AUTOSTART_UPDATE_FAILED", str(exc), 409) from exc
        return {"success": True, "autostart": status}

    def get_bridge_status(self, account: str) -> dict:
        config = self._load_account(account)
        try:
            lifecycle = self._bridge_status_reader(config)
        except Exception as exc:
            raise ServiceError("BRIDGE_STATUS_FAILED", str(exc), 409) from exc
        return {"success": True, "lifecycle": lifecycle}

    def start_account_bridge(self, account: str) -> dict:
        self.require_enabled_capability("account-start")
        config = self._load_account(account)
        try:
            lifecycle = self._bridge_starter(config)
        except Exception as exc:
            raise ServiceError("BRIDGE_START_FAILED", str(exc), 409) from exc
        return {"success": True, "lifecycle": lifecycle}

    def stop_account_bridge(self, account: str) -> dict:
        self.require_enabled_capability("account-start")
        config = self._load_account(account)
        try:
            lifecycle = self._bridge_stopper(config)
        except Exception as exc:
            raise ServiceError("BRIDGE_STOP_FAILED", str(exc), 409) from exc
        return {"success": True, "lifecycle": lifecycle}

    def restart_account_bridge(self, account: str) -> dict:
        self.require_enabled_capability("account-start")
        config = self._load_account(account)
        try:
            lifecycle = self._bridge_restarter(config)
        except Exception as exc:
            raise ServiceError("BRIDGE_RESTART_FAILED", str(exc), 409) from exc
        return {"success": True, "lifecycle": lifecycle}

    def export_diagnostics(self) -> dict:
        diagnosis = self.doctor_account()
        try:
            target = self._diagnostic_exporter(
                self._store.root,
                diagnosis=diagnosis,
                system=self.system_status(),
                version=PRODUCT_VERSION,
            )
        except OSError as exc:
            raise ServiceError("DIAGNOSTIC_EXPORT_FAILED", str(exc), 409) from exc
        return {
            "success": True,
            "path": str(target),
            "message": "诊断报告已保存到本机，敏感字段已移除",
        }

    def create_task(self, **values) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        return {"success": True, "task": self.tasks.create(**values)}

    def list_tasks(self) -> dict:
        return {"success": True, "tasks": self.tasks.list()}

    def get_task(self, task_id: str) -> dict:
        return {"success": True, "task": self.tasks.get(task_id)}

    def execute_task(self, task_id: str) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        task = self.tasks.get(task_id)
        if task["state"] != "QUEUED":
            raise ServiceError("INVALID_TASK_STATE", "只有排队中的任务可以执行", 409)
        status = self.get_account_status(task["account_slot"])
        if not status["ready"]:
            task = self.tasks.transition(
                task_id,
                "BLOCKED",
                error_code="ACCOUNT_NOT_READY",
                recommended_action=status["next_action"] or "检查账号状态",
            )
            return {"success": True, "task": task}
        parameters = task.get("parameters") or {}
        target_key = str(
            parameters.get("feed_id")
            or parameters.get("user_id")
            or parameters.get("keyword")
            or ""
        )
        if task["risk_level"] == "L1":
            self.quota.check_l1(
                account=task["account_slot"],
                capability=task["capability"],
                target_key=target_key,
            )
        self.tasks.transition(task_id, "RUNNING")
        try:
            result = self.runner.execute(
                task["account_slot"], task["capability"], parameters
            )
        except ServiceError as exc:
            final = "RESULT_UNKNOWN" if task["risk_level"] == "L1" else "FAILED"
            completed = self.tasks.transition(
                task_id,
                final,
                result_summary=exc.message,
                error_code=exc.code,
                recommended_action=(
                    "先回读平台当前状态，再决定是否重试"
                    if final == "RESULT_UNKNOWN"
                    else "检查账号和任务参数后重试"
                ),
            )
            self._record_task_event(completed, target_key)
            return {"success": True, "task": completed}
        completed = self.tasks.transition(
            task_id,
            "SUCCESS",
            result_summary=self._result_summary(result),
        )
        self._record_task_event(completed, target_key, result=result)
        return {"success": True, "task": completed, "result": result}

    def list_records(self) -> dict:
        records = sorted(
            self._store.list("events"),
            key=lambda item: item.get("finished_at") or "",
            reverse=True,
        )
        return {"success": True, "records": records}

    def _record_task_event(self, task: dict, target_key: str, *, result=None) -> None:
        import uuid

        event_id = uuid.uuid4().hex
        event = {
            "event_id": event_id,
            "task_id": task["task_id"],
            "account_slot": task["account_slot"],
            "capability": task["capability"],
            "risk_level": task["risk_level"],
            "target_key": target_key,
            "state": task["state"],
            "started_at": task["started_at"],
            "finished_at": task["finished_at"],
            "result_summary": task["result_summary"],
            "error_code": task["error_code"],
            "result": result or {},
        }
        self._store.put("events", event_id, event)

    @staticmethod
    def _result_summary(result: dict) -> str:
        if result.get("message"):
            return str(result["message"])
        if "count" in result:
            return f"完成，共 {result['count']} 条结果"
        return "任务执行成功"

    def create_draft(self, **values) -> dict:
        return {"success": True, "draft": self.approvals.create_draft(**values)}

    def list_drafts(self) -> dict:
        drafts = sorted(
            self._store.list("drafts"),
            key=lambda item: item["updated_at"],
            reverse=True,
        )
        return {"success": True, "drafts": drafts}

    def update_draft(self, draft_id: str, **changes) -> dict:
        return {
            "success": True,
            "draft": self.approvals.update_draft(draft_id, **changes),
        }

    def confirm_draft(self, draft_id: str, *, ttl_seconds: int = 300) -> dict:
        return {
            "success": True,
            "approval": self.approvals.confirm(draft_id, ttl_seconds=ttl_seconds),
        }

    def execute_draft(
        self,
        draft_id: str,
        *,
        approval_id: str,
        xsec_token: str,
        feed_id: str,
        comment_id: str = "",
        user_id: str = "",
    ) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        draft = self._store.get("drafts", draft_id)
        if draft is None:
            raise ServiceError("NOT_FOUND", "草稿不存在", 404)
        execution_target = feed_id if draft["action_type"] == "post-comment" else comment_id
        if execution_target != draft["target_id"]:
            raise ServiceError("CONFIRMATION_MISMATCH", "执行目标与已确认草稿不一致", 409)
        if not xsec_token.strip() or not feed_id.strip():
            raise ServiceError("INVALID_REQUEST", "执行评论或回复需要笔记信息")
        status = self.get_account_status(draft["account_slot"])
        if not status["ready"]:
            raise ServiceError("ACCOUNT_NOT_READY", status["next_action"] or "账号未就绪", 409)
        live_uid = status["identity"].get("live_user_id") or ""
        consumed = self.approvals.consume(
            approval_id,
            account_slot=draft["account_slot"],
            verified_uid=live_uid,
            action_type=draft["action_type"],
            target_id=draft["target_id"],
        )
        task = self.tasks.create(
            source="webui",
            account_slot=draft["account_slot"],
            capability=draft["action_type"],
            request_summary=f"确认后执行 {draft['action_type']}",
            parameters={
                "feed_id": feed_id,
                "xsec_token": xsec_token,
                "comment_id": comment_id,
                "user_id": user_id,
                "content": draft["content"],
            },
        )
        self.tasks.transition(task["task_id"], "QUEUED")
        self.tasks.transition(task["task_id"], "RUNNING")
        try:
            result = self.runner.execute(
                draft["account_slot"], draft["action_type"], task["parameters"]
            )
        except Exception as exc:
            code = exc.code if isinstance(exc, ServiceError) else "EXECUTION_ERROR"
            message = exc.message if isinstance(exc, ServiceError) else str(exc)
            completed = self.tasks.transition(
                task["task_id"],
                "RESULT_UNKNOWN",
                result_summary=message,
                error_code=code,
                recommended_action="请先在小红书页面人工检查结果，不要直接重发",
            )
            self._record_task_event(completed, draft["target_id"])
            return {"success": True, "task": completed, "approval": consumed["approval"]}
        completed = self.tasks.transition(
            task["task_id"], "SUCCESS", result_summary=self._result_summary(result)
        )
        self._record_task_event(completed, draft["target_id"], result=result)
        return {
            "success": True,
            "task": completed,
            "result": result,
            "approval": consumed["approval"],
        }

    def get_account_status(self, account: str, *, bridge_url: str | None = None) -> dict:
        config = self._load_account(account)
        page = self._page_factory(bridge_url or config.bridge_url, account=config.name)
        bridge_status = page.get_server_status()
        runtime = evaluate_profile_connection(config, bridge_status)
        identity = self._identity_summary(config.name)
        state, next_action = _account_state(runtime, identity)
        connection_ready = bool(
            runtime["bridge_running"]
            and runtime["extension_connected"]
            and runtime["profile_verified"]
        )
        return {
            "success": True,
            "server_running": runtime["bridge_running"],
            "extension_connected": runtime["extension_connected"],
            "profile_verified": runtime["profile_verified"],
            "connection_ready": connection_ready,
            "ready": state == "READY",
            "status": state,
            "next_action": next_action,
            "runtime": runtime,
            "identity": identity,
            "bridge": bridge_status,
            "account": public_config(config),
        }

    def doctor_account(self, account: str | None = None) -> dict:
        return self._diagnostic(account, page_factory=self._page_factory)

    def _load_account(self, account: str) -> AccountConfig:
        try:
            return self._account_loader(account)
        except (FileNotFoundError, ValueError) as exc:
            raise ServiceError("ACCOUNT_NOT_FOUND", str(exc), 404) from exc

    def _ensure_slot_capacity(self) -> None:
        if len(self._account_lister()) >= 6:
            raise ServiceError("ACCOUNT_LIMIT_REACHED", "V1 最多支持 6 个账号槽位", 409)

    def _identity_summary(self, account: str) -> dict:
        record = self._identity_loader(account)
        current = (record or {}).get("current") or {}
        runtime_checks = self._store.get_setting("runtime_identity_checks", {})
        live = runtime_checks.get(account) or {}
        recorded_uid = str(current.get("user_id") or "")
        live_uid = str(live.get("user_id") or "")
        return {
            "recorded": bool(record),
            "user_id": recorded_uid,
            "nickname": str(current.get("nickname") or ""),
            "observed_at": str(current.get("observed_at") or ""),
            "live_checked": bool(live_uid),
            "live_user_id": live_uid,
            "live_nickname": str(live.get("nickname") or ""),
            "matches_record": bool(recorded_uid and live_uid == recorded_uid),
        }


def _account_state(runtime: dict, identity: dict) -> tuple[str, str | None]:
    if not runtime["bridge_running"]:
        return "BLOCKED", "启动或检查该账号的 Bridge"
    if not runtime["extension_connected"]:
        return "BLOCKED", "手动打开对应 Chrome Profile 并保持扩展在线"
    if not runtime["profile_verified"]:
        return "BLOCKED", "检查 Profile 绑定和扩展配对"
    if not identity["recorded"]:
        return "IDENTITY_REQUIRED", "在 WebUI 中完成登录身份核验"
    if not identity["live_checked"]:
        return "IDENTITY_CHECK_REQUIRED", "运行只读登录和当前 UID 核验后进入 READY"
    if not identity["matches_record"]:
        return "IDENTITY_MISMATCH", "当前登录账号与槽位记录不一致，请停止任务并检查账号"
    return "READY", None
