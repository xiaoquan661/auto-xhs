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
from account_identity import (
    begin_login_switch,
    cancel_login_switch,
    complete_login_switch,
    load_identity_history,
    load_identity_record,
    load_switch_state,
    record_current_identity,
)
from account_lifecycle import restart_account_runtime, start_account_runtime
from account_manager import (
    AccountConfig,
    add_account,
    archive_account,
    discover_chrome_profiles,
    import_existing_profile,
    list_accounts,
    load_account,
    public_config,
)
from account_pairing import (
    create_pairing_session,
    get_pairing_status,
    revoke_account_pairing,
)
from account_runtime import evaluate_profile_connection
from approval_service import ApprovalService
from bridge_lifecycle import (
    get_bridge_lifecycle,
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
from xhs.errors import XHSError
from xhs.login import get_current_user_identity, logout

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
        switch_loader: Callable[[str], dict | None] = load_switch_state,
        switch_history_loader: Callable[..., list[dict]] = load_identity_history,
        identity_observer: Callable[[object], dict] = get_current_user_identity,
        account_creator: Callable[..., AccountConfig] = add_account,
        account_archiver: Callable[[str], dict] = archive_account,
        profile_importer: Callable[..., AccountConfig] = import_existing_profile,
        profile_discoverer: Callable[..., list[dict]] = discover_chrome_profiles,
        identity_recorder: Callable[..., dict] = record_current_identity,
        switch_beginner: Callable[..., dict] = begin_login_switch,
        switch_completer: Callable[..., dict] = complete_login_switch,
        switch_canceller: Callable[..., dict] = cancel_login_switch,
        account_logout: Callable[[object], bool] = logout,
        product_store: ProductStore | None = None,
        extension_source: str | Path = DEFAULT_EXTENSION_SOURCE,
        business_runner: BusinessRunner | None = None,
        bridge_status_reader: Callable[..., dict] = get_bridge_lifecycle,
        bridge_starter: Callable[..., dict] = start_account_runtime,
        bridge_stopper: Callable[..., dict] = stop_bridge,
        bridge_restarter: Callable[..., dict] = restart_account_runtime,
        diagnostic_exporter: Callable[..., Path] = export_diagnostic_report,
        autostart_reader: Callable[[str], dict] = account_autostart_status,
        autostart_enabler: Callable[[str], dict] = enable_account_autostart,
        autostart_disabler: Callable[[str], dict] = disable_account_autostart,
        pairing_revoker: Callable[[str], AccountConfig] = revoke_account_pairing,
    ) -> None:
        self._account_lister = account_lister
        self._account_loader = account_loader
        self._diagnostic = diagnostic
        self._page_factory = page_factory
        self._identity_loader = identity_loader
        self._switch_loader = switch_loader
        self._switch_history_loader = switch_history_loader
        self._identity_observer = identity_observer
        self._account_creator = account_creator
        self._account_archiver = account_archiver
        self._profile_importer = profile_importer
        self._profile_discoverer = profile_discoverer
        self._identity_recorder = identity_recorder
        self._switch_beginner = switch_beginner
        self._switch_completer = switch_completer
        self._switch_canceller = switch_canceller
        self._account_logout = account_logout
        self._extension_source = Path(extension_source).resolve()
        self._bridge_status_reader = bridge_status_reader
        self._bridge_starter = bridge_starter
        self._bridge_stopper = bridge_stopper
        self._bridge_restarter = bridge_restarter
        self._diagnostic_exporter = diagnostic_exporter
        self._autostart_reader = autostart_reader
        self._autostart_enabler = autostart_enabler
        self._autostart_disabler = autostart_disabler
        self._pairing_revoker = pairing_revoker
        self._store = product_store or ProductStore()
        self.tasks = TaskService(self._store)
        self.tasks.recover_interrupted()
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
            "next_action": "点击“启动账号”打开新 Profile，再完成扩展配对",
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
            "next_action": "点击“启动账号”打开该 Profile，再完成扩展配对",
        }

    def remove_account_slot(
        self,
        account: str,
        *,
        confirmed: bool,
        confirmation_name: str,
    ) -> dict:
        """Archive one local slot without deleting Chrome or XHS login data."""
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "删除账号槽位需要明确确认", 409)
        if confirmation_name.strip() != account:
            raise ServiceError(
                "CONFIRMATION_MISMATCH",
                f"请输入完整槽位名称 {account!r} 进行确认",
                409,
            )
        self.require_enabled_capability("account-remove")
        config = self._load_account(account)

        account_tasks = [
            task for task in self.tasks.list() if task.get("account_slot") == account
        ]
        if any(task.get("state") == "RUNNING" for task in account_tasks):
            raise ServiceError(
                "ACCOUNT_BUSY",
                "该槽位仍有任务正在运行，请等待任务结束后再删除",
                409,
            )
        try:
            lifecycle = self._bridge_status_reader(config)
        except Exception as exc:
            raise ServiceError("BRIDGE_STATUS_FAILED", str(exc), 409) from exc
        if lifecycle.get("bridge_running") and not lifecycle.get("registered"):
            raise ServiceError(
                "BRIDGE_NOT_MANAGED",
                "该槽位端口上有未登记的 Bridge 进程；请先在诊断中确认并手动停止",
                409,
            )

        cancelled_task_ids = []
        for task in account_tasks:
            if task.get("state") in {"QUEUED", "WAITING_APPROVAL", "BLOCKED"}:
                self.cancel_task(task["task_id"])
                cancelled_task_ids.append(task["task_id"])

        local_binding_cleared = False
        bridge_stopped = False
        try:
            if lifecycle.get("bridge_running"):
                page = self._page_for_account(config)
                if page.is_extension_connected():
                    local_binding_cleared = bool(page.clear_extension_binding())
                self._bridge_stopper(config)
                bridge_stopped = True
            self._autostart_disabler(account)
            self._pairing_revoker(account)
            archive = self._account_archiver(account)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("ACCOUNT_REMOVE_FAILED", str(exc), 409) from exc

        self._clear_runtime_identity_check(account)
        return {
            "success": True,
            "account": account,
            "archived": True,
            "archive": archive,
            "cancelled_task_ids": cancelled_task_ids,
            "bridge_stopped": bridge_stopped,
            "autostart_disabled": True,
            "local_binding_cleared": local_binding_cleared,
            "preserved": ["Chrome Profile", "小红书登录数据", "共享通用扩展"],
            "message": "账号槽位已移出列表并保存到本机归档",
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
        page = self._page_for_account(config)
        identity = self._observe_identity(page)
        self._remember_runtime_identity(account, identity)
        return {"success": True, "account": account, "identity": identity}

    def record_account_identity(self, account: str, *, confirmed: bool, label: str = "") -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "记录当前 UID 需要明确确认", 409)
        self.require_enabled_capability("account-identity", operation="record")
        if self._switch_loader(account):
            raise ServiceError(
                "ACCOUNT_SWITCH_PENDING",
                "账号正在换号，不能重新记录 UID；请完成或取消当前换号流程",
                409,
            )
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

    def get_account_switch(self, account: str) -> dict:
        self._load_account(account)
        try:
            pending = self._switch_loader(account)
            history = self._switch_history_loader(account, limit=10)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("ACCOUNT_SWITCH_STATUS_FAILED", str(exc), 409) from exc
        return {
            "success": True,
            "account": account,
            "pending": pending,
            "history": history,
        }

    def logout_account(self, account: str, *, confirmed: bool) -> dict:
        """Exit one XHS session and verify the logged-out state from the page."""
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "退出登录需要明确确认", 409)
        self.require_enabled_capability("delete-cookies")
        config = self._load_account(account)
        page = self._page_for_account(config)
        try:
            logged_out = self._account_logout(page)
            observed_after = self._observe_identity(page, require_uid=False)
            if observed_after.get("logged_in"):
                raise RuntimeError("退出后仍检测到原账号登录，操作未完成")
        except (OSError, RuntimeError, ValueError, XHSError) as exc:
            raise ServiceError("ACCOUNT_LOGOUT_FAILED", str(exc), 409) from exc
        self._clear_runtime_identity_check(account)
        return {
            "success": True,
            "account": account,
            "logged_out": logged_out,
            "verified_logged_out": True,
            "identity": observed_after,
            "message": "已退出登录" if logged_out else "当前账号未登录，无需退出",
        }

    def begin_account_switch(
        self,
        account: str,
        *,
        confirmed: bool,
        target_user_id: str = "",
        label: str = "",
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "开始换号需要明确确认", 409)
        self.require_enabled_capability("account-switch-begin")
        config = self._load_account(account)
        page = self._page_for_account(config)
        observed = self._observe_identity(page)
        pending = None
        try:
            pending = self._switch_beginner(
                account,
                observed,
                target_user_id=target_user_id,
                target_label=label,
            )
            logged_out = self._account_logout(page)
            if not logged_out:
                raise RuntimeError("自动退出未完成，请重试换号操作")
            observed_after = self._observe_identity(page, require_uid=False)
            if observed_after.get("logged_in"):
                raise RuntimeError("退出后仍检测到旧账号登录，换号流程未开始")
        except ServiceError:
            if pending is not None:
                self._switch_canceller(account, observed, force=False)
            raise
        except (OSError, RuntimeError, ValueError, XHSError) as exc:
            if pending is not None:
                self._switch_canceller(account, observed, force=False)
            raise ServiceError("ACCOUNT_SWITCH_BEGIN_FAILED", str(exc), 409) from exc
        self._clear_runtime_identity_check(account)
        return {
            "success": True,
            "account": account,
            "switch": pending,
            "logged_out": logged_out,
            "verified_logged_out": True,
            "identity": observed_after,
            "business_tasks_blocked": True,
            "next_action": "请在该槽位对应的 Chrome Profile 中登录新账号",
        }

    def complete_account_switch(
        self,
        account: str,
        *,
        confirmed: bool,
        expected_user_id: str = "",
        label: str = "",
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "完成换号需要明确确认", 409)
        self.require_enabled_capability("account-switch-complete")
        config = self._load_account(account)
        page = self._page_for_account(config)
        observed = self._observe_identity(page)
        try:
            event = self._switch_completer(
                account,
                observed,
                expected_user_id=expected_user_id,
                label=label,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("ACCOUNT_SWITCH_COMPLETE_FAILED", str(exc), 409) from exc
        self._remember_runtime_identity(account, observed)
        return {
            "success": True,
            "account": account,
            "switch": event,
            "business_tasks_blocked": False,
            "message": "新登录身份已核验并绑定，业务任务已恢复",
        }

    def cancel_account_switch(self, account: str, *, confirmed: bool) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "取消换号需要明确确认", 409)
        self.require_enabled_capability("account-switch-cancel")
        config = self._load_account(account)
        page = self._page_for_account(config)
        observed = self._observe_identity(page, require_uid=False)
        try:
            result = self._switch_canceller(account, observed, force=False)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("ACCOUNT_SWITCH_CANCEL_FAILED", str(exc), 409) from exc
        self._clear_runtime_identity_check(account)
        return {"success": True, "account": account, "switch": result}

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
            raise ServiceError("CONFIRMATION_REQUIRED", "修改账号自启动需要明确确认", 409)
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
        self._validate_task_parameters(
            str(values.get("capability") or ""),
            values.get("parameters") or {},
        )
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
        target_key = self._task_target_key(task)
        status = self.get_account_status(task["account_slot"])
        if not status["ready"]:
            task = self.tasks.transition(
                task_id,
                "BLOCKED",
                error_code="ACCOUNT_NOT_READY",
                recommended_action=status["next_action"] or "检查账号状态",
            )
            self._record_task_event(task, target_key)
            return {"success": True, "task": task}
        parameters = dict(task.get("parameters") or {})
        if task["risk_level"] == "L1":
            try:
                if task["capability"] == "keyword-engagement":
                    action = str(parameters.get("action") or "")
                    requested = int(parameters.get("count") or 0)
                    requested_actions = requested * (2 if action == "both" else 1)
                    self.quota.check_l1_batch(
                        account=task["account_slot"],
                        requested_actions=requested_actions,
                    )
                    parameters["excluded_by_action"] = {
                        "like": sorted(
                            self.quota.recent_target_ids(
                                account=task["account_slot"], capability="like-feed"
                            )
                        ),
                        "favorite": sorted(
                            self.quota.recent_target_ids(
                                account=task["account_slot"], capability="favorite-feed"
                            )
                        ),
                    }
                else:
                    self.quota.check_l1(
                        account=task["account_slot"],
                        capability=task["capability"],
                        target_key=target_key,
                    )
            except ServiceError as exc:
                blocked = self.tasks.transition(
                    task_id,
                    "BLOCKED",
                    error_code=exc.code,
                    recommended_action=exc.message,
                )
                self._record_task_event(blocked, target_key)
                return {"success": True, "task": blocked}
        claimed = self.tasks.claim_for_execution(task_id)
        if claimed["state"] == "BLOCKED":
            self._record_task_event(claimed, target_key)
            return {"success": True, "task": claimed}
        try:
            result = self.runner.execute(
                task["account_slot"], task["capability"], parameters
            )
        except Exception as exc:
            final = "RESULT_UNKNOWN" if task["risk_level"] == "L1" else "FAILED"
            code = exc.code if isinstance(exc, ServiceError) else "EXECUTION_ERROR"
            message = exc.message if isinstance(exc, ServiceError) else str(exc)
            completed = self.tasks.transition(
                task_id,
                final,
                result_summary=message,
                error_code=code,
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
            "PARTIAL_SUCCESS" if result.get("partial") else "SUCCESS",
            result_summary=self._result_summary(result),
        )
        self._record_task_event(completed, target_key, result=result)
        return {"success": True, "task": completed, "result": result}

    def retry_task(self, task_id: str) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        task = self.tasks.get(task_id)
        if task["state"] != "BLOCKED":
            raise ServiceError("INVALID_TASK_STATE", "只有需处理的任务可以重试", 409)
        self.tasks.transition(task_id, "QUEUED")
        return self.execute_task(task_id)

    def cancel_task(self, task_id: str) -> dict:
        task = self.tasks.get(task_id)
        if task["state"] not in {"QUEUED", "WAITING_APPROVAL", "BLOCKED"}:
            raise ServiceError(
                "INVALID_TASK_STATE",
                "只有尚未开始或需处理的任务可以取消",
                409,
            )
        cancelled = self.tasks.transition(
            task_id,
            "CANCELLED",
            result_summary="任务已由用户取消",
        )
        self._record_task_event(cancelled, self._task_target_key(cancelled))
        return {"success": True, "task": cancelled}

    def list_records(self) -> dict:
        records = sorted(
            self._store.list("events"),
            key=lambda item: item.get("finished_at") or "",
            reverse=True,
        )
        return {"success": True, "records": records}

    def _record_task_event(self, task: dict, target_key: str, *, result=None) -> None:
        self.tasks.record_event(task, target_key, result=result)

    @staticmethod
    def _task_target_key(task: dict) -> str:
        parameters = task.get("parameters") or {}
        return str(
            parameters.get("feed_id")
            or parameters.get("user_id")
            or parameters.get("keyword")
            or ""
        )

    @staticmethod
    def _validate_task_parameters(capability: str, parameters: dict) -> None:
        if capability in {"fill-publish", "fill-publish-video", "long-article"}:
            raise ServiceError(
                "AGENT_CLI_ONLY",
                "发布任务只能由 Agent 通过 Python CLI 的预览流程创建",
                409,
            )
        if capability == "browse-feeds":
            try:
                duration_minutes = int(parameters.get("duration_minutes"))
                count = int(parameters.get("count"))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "自动浏览时间和点开数量必须是整数") from exc
            if not 1 <= duration_minutes <= 120:
                raise ServiceError("INVALID_REQUEST", "自动浏览时间必须在 1 到 120 分钟之间")
            if not 1 <= count <= 50:
                raise ServiceError("INVALID_REQUEST", "自动浏览点开数量必须在 1 到 50 篇之间")
        if capability == "search-feeds" and not str(parameters.get("keyword") or "").strip():
            raise ServiceError("INVALID_REQUEST", "搜索任务必须填写关键词")
        if capability == "keyword-engagement":
            if not str(parameters.get("keyword") or "").strip():
                raise ServiceError("INVALID_REQUEST", "随机点赞收藏任务必须填写筛选关键词")
            if str(parameters.get("action") or "") not in {"like", "favorite", "both"}:
                raise ServiceError("INVALID_REQUEST", "请选择点赞、收藏或点赞并收藏")
            try:
                count = int(parameters.get("count"))
                candidate_pool_size = int(parameters.get("candidate_pool_size", 20))
                collection_minutes = int(parameters.get("collection_minutes", 2))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "随机互动、候选池和搜集时间必须是整数") from exc
            if not 1 <= count <= 20:
                raise ServiceError("INVALID_REQUEST", "随机互动数量必须在 1 到 20 篇之间")
            if not count <= candidate_pool_size <= 100:
                raise ServiceError("INVALID_REQUEST", "候选池数量必须不少于互动数量，且不超过 100 篇")
            if not 1 <= collection_minutes <= 10:
                raise ServiceError("INVALID_REQUEST", "最长搜集时间必须在 1 到 10 分钟之间")
        if capability == "random-comment":
            if parameters.get("direct_send_authorized") is not True:
                raise ServiceError(
                    "CONFIRMATION_REQUIRED",
                    "随机评论会直接发送，必须确认本次任务授权",
                    409,
                )
            if str(parameters.get("style") or "natural") not in {
                "natural",
                "praise",
                "question",
            }:
                raise ServiceError("INVALID_REQUEST", "请选择有效的随机评论风格")
            try:
                count = int(parameters.get("count"))
                candidate_pool_size = int(parameters.get("candidate_pool_size", 20))
                collection_minutes = int(parameters.get("collection_minutes", 2))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "评论数量、候选池和搜集时间必须是整数") from exc
            if not 1 <= count <= 3:
                raise ServiceError("INVALID_REQUEST", "随机评论数量必须在 1 到 3 篇之间")
            if not count <= candidate_pool_size <= 100:
                raise ServiceError("INVALID_REQUEST", "候选池数量必须不少于评论数量，且不超过 100 篇")
            if not 1 <= collection_minutes <= 10:
                raise ServiceError("INVALID_REQUEST", "最长搜集时间必须在 1 到 10 分钟之间")
        if capability == "get-feed-detail":
            if not str(parameters.get("feed_id") or "").strip():
                raise ServiceError("INVALID_REQUEST", "查看笔记详情必须填写笔记 ID")
            if not str(parameters.get("xsec_token") or "").strip():
                raise ServiceError("INVALID_REQUEST", "查看笔记详情必须填写 XSEC Token")
        if capability == "user-profile":
            if not str(parameters.get("user_id") or "").strip():
                raise ServiceError("INVALID_REQUEST", "查看用户主页必须填写用户 ID")
            if not str(parameters.get("xsec_token") or "").strip():
                raise ServiceError("INVALID_REQUEST", "查看用户主页必须填写 XSEC Token")
        if capability in {"like-feed", "favorite-feed"}:
            if not str(parameters.get("feed_id") or "").strip():
                raise ServiceError("INVALID_REQUEST", "点赞或收藏任务必须填写笔记 ID")
            if not str(parameters.get("xsec_token") or "").strip():
                raise ServiceError("INVALID_REQUEST", "点赞或收藏任务必须填写 XSEC Token")

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
        claimed = self.tasks.claim_for_execution(task["task_id"])
        if claimed["state"] == "BLOCKED":
            self._record_task_event(claimed, draft["target_id"])
            return {
                "success": True,
                "task": claimed,
                "approval": consumed["approval"],
            }
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
            self.approvals.mark_execution(draft_id, "RESULT_UNKNOWN")
            self._record_task_event(completed, draft["target_id"])
            return {"success": True, "task": completed, "approval": consumed["approval"]}
        completed = self.tasks.transition(
            task["task_id"], "SUCCESS", result_summary=self._result_summary(result)
        )
        self.approvals.mark_execution(draft_id, "EXECUTED")
        self._record_task_event(completed, draft["target_id"], result=result)
        return {
            "success": True,
            "task": completed,
            "result": result,
            "approval": consumed["approval"],
        }

    def get_account_status(self, account: str, *, bridge_url: str | None = None) -> dict:
        config = self._load_account(account)
        page = self._page_for_account(config, bridge_url=bridge_url)
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

    def _page_for_account(
        self,
        config: AccountConfig,
        *,
        bridge_url: str | None = None,
    ) -> BridgePage:
        return self._page_factory(
            bridge_url or config.bridge_url,
            account=config.name,
            account_id=config.account_id,
            bridge_token=config.bridge_token,
        )

    def _ensure_slot_capacity(self) -> None:
        if len(self._account_lister()) >= 6:
            raise ServiceError("ACCOUNT_LIMIT_REACHED", "V1 最多支持 6 个账号槽位", 409)

    def _identity_summary(self, account: str) -> dict:
        record = self._identity_loader(account)
        switch = self._switch_loader(account)
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
            "switch_pending": bool(switch),
            "switch": switch,
        }

    def _observe_identity(self, page: object, *, require_uid: bool = True) -> dict:
        try:
            identity = self._identity_observer(page)
        except Exception as exc:
            raise ServiceError("IDENTITY_CHECK_FAILED", str(exc), 409) from exc
        if identity.get("error"):
            raise ServiceError(
                "IDENTITY_CHECK_FAILED",
                f"无法读取当前登录身份：{identity['error']}",
                409,
            )
        if require_uid and not identity.get("logged_in"):
            raise ServiceError("LOGIN_REQUIRED", "当前 Profile 尚未登录小红书", 409)
        if require_uid and not identity.get("user_id"):
            raise ServiceError(
                "IDENTITY_UID_UNAVAILABLE",
                "已检测到登录，但无法读取当前 UID，请刷新小红书首页后重试",
                409,
            )
        return identity

    def _remember_runtime_identity(self, account: str, identity: dict) -> None:
        checks = dict(self._store.get_setting("runtime_identity_checks", {}))
        checks[account] = {
            "user_id": str(identity.get("user_id") or ""),
            "nickname": str(identity.get("nickname") or ""),
            "observed_at": str(identity.get("observed_at") or ""),
        }
        self._store.set_setting("runtime_identity_checks", checks)

    def _clear_runtime_identity_check(self, account: str) -> None:
        checks = dict(self._store.get_setting("runtime_identity_checks", {}))
        checks.pop(account, None)
        self._store.set_setting("runtime_identity_checks", checks)


def _account_state(runtime: dict, identity: dict) -> tuple[str, str | None]:
    if not runtime["bridge_running"]:
        return "BLOCKED", "点击“启动账号”恢复 Bridge 和绑定的 Chrome Profile"
    if not runtime["extension_connected"]:
        return "BLOCKED", "点击“启动账号”，再检查扩展是否已加载和配对"
    if not runtime["profile_verified"]:
        return "BLOCKED", "检查 Profile 绑定和扩展配对"
    if identity["switch_pending"]:
        return "SWITCH_PENDING", "在对应 Chrome Profile 登录新账号，然后完成换号核验"
    if not identity["recorded"]:
        return "IDENTITY_REQUIRED", "在 WebUI 中完成登录身份核验"
    if not identity["live_checked"]:
        return "IDENTITY_CHECK_REQUIRED", "运行只读登录和当前 UID 核验后进入 READY"
    if not identity["matches_record"]:
        return "IDENTITY_MISMATCH", "当前登录账号与槽位记录不一致，请停止任务并检查账号"
    return "READY", None
