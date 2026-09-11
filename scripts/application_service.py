"""Shared read-only application services for CLI, WebUI, and agents."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from pathlib import Path

from account_doctor import diagnose_accounts
from account_identity import (
    begin_login_switch,
    cancel_login_switch,
    complete_login_switch,
    load_identity_history,
    load_identity_record,
    load_switch_state,
    record_current_identity,
    replace_current_identity,
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
    set_unpaired_account_platform,
)
from account_pairing import (
    create_pairing_session,
    get_pairing_status,
    revoke_account_pairing,
)
from account_runtime import evaluate_profile_connection
from action_workflow_service import ActionWorkflowService
from approval_service import ApprovalService
from application_memory import ApplicationMemory
from bridge_lifecycle import (
    get_bridge_lifecycle,
    start_bridge,
    stop_bridge,
)
from business_runner import BusinessRunner
from capability_registry import (
    CapabilityPolicy,
    get_operation_policy,
    list_capability_policies,
)
from collector_service import CollectorService
from comment_collector import CommentCollector
from conversation_ownership_service import OWNER_HUMAN, ConversationOwnershipService
from data_recovery_service import DataRecoveryService
from diagnostic_export import export_diagnostic_report
from inbound_event_service import InboundEventService
from metric_service import MetricService
from operations_db import OperationsDatabase
from operations_policy import check_operations_policy
from passive_reply_service import PassiveReplyService
from platforms import (
    CapabilitySupport,
    get_platform_capability,
    list_platform_capabilities,
    list_platform_specs,
    normalize_platform,
)
from private_message_batch import normalize_private_message_recipients
from private_message_collector import PrivateMessageCollector
from product_store import ProductStore
from publish_workflow import PublishWorkflowService
from quota_service import QuotaService
from reply_cycle_service import ReplyCycleService
from reply_intelligence_service import ReplyIntelligenceService
from reply_llm_client import OpenAICompatibleReplyClient, ReplyModelConfig
from reply_rule_service import ReplyRuleService
from report_service import ReportService
from service_errors import ServiceError
from task_service import TaskService
from xhs.bridge import BridgePage
from xhs.errors import PublishError, XHSError
from xhs.login import get_current_user_identity, logout

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXTENSION_SOURCE = PROJECT_ROOT / "extension"
PRODUCT_VERSION = "0.1.0-v1"


class ApplicationService(ApplicationMemory):
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
        identity_replacer: Callable[..., dict] = replace_current_identity,
        switch_beginner: Callable[..., dict] = begin_login_switch,
        switch_completer: Callable[..., dict] = complete_login_switch,
        switch_canceller: Callable[..., dict] = cancel_login_switch,
        account_logout: Callable[[object], bool] = logout,
        product_store: ProductStore | None = None,
        operations_database: OperationsDatabase | None = None,
        extension_source: str | Path = DEFAULT_EXTENSION_SOURCE,
        business_runner: BusinessRunner | None = None,
        bridge_status_reader: Callable[..., dict] = get_bridge_lifecycle,
        bridge_starter: Callable[..., dict] = start_account_runtime,
        pairing_bridge_starter: Callable[..., dict] = start_bridge,
        bridge_stopper: Callable[..., dict] = stop_bridge,
        bridge_restarter: Callable[..., dict] = restart_account_runtime,
        diagnostic_exporter: Callable[..., Path] = export_diagnostic_report,
        pairing_creator: Callable[..., dict] = create_pairing_session,
        pairing_status_reader: Callable[[str], dict] = get_pairing_status,
        pairing_revoker: Callable[[str], AccountConfig] = revoke_account_pairing,
        reply_intelligence: ReplyIntelligenceService | None = None,
        webui_restarter: Callable[[], dict] | None = None,
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
        self._identity_replacer = identity_replacer
        self._switch_beginner = switch_beginner
        self._switch_completer = switch_completer
        self._switch_canceller = switch_canceller
        self._account_logout = account_logout
        self._extension_source = Path(extension_source).resolve()
        self._bridge_status_reader = bridge_status_reader
        self._bridge_starter = bridge_starter
        self._pairing_bridge_starter = pairing_bridge_starter
        self._bridge_stopper = bridge_stopper
        self._bridge_restarter = bridge_restarter
        self._diagnostic_exporter = diagnostic_exporter
        self._pairing_creator = pairing_creator
        self._pairing_status_reader = pairing_status_reader
        self._pairing_revoker = pairing_revoker
        self._webui_restarter = webui_restarter
        self._runtime_instance_id = uuid.uuid4().hex
        self._store = product_store or ProductStore()
        self.operations_database = operations_database or OperationsDatabase(root=self._store.root)
        self.inbound_events = InboundEventService(self.operations_database)
        self.collectors = CollectorService(self.operations_database)
        self.private_message_conversations = ConversationOwnershipService(self.operations_database)
        self.private_message_conversations.backfill_latest_decisions()
        self.comment_collector = CommentCollector(self.inbound_events, self.collectors)
        self.private_message_collector = PrivateMessageCollector(
            self.inbound_events,
            self.collectors,
            self.private_message_conversations,
        )
        self.metrics = MetricService(self.operations_database)
        self.reports = ReportService(self.operations_database)
        self.data_recovery = DataRecoveryService(self.operations_database)
        self.published_posts = self.data_recovery.published_posts
        self.operation_events = self.data_recovery.operation_events
        self.reply_records = self.data_recovery.reply_records
        self.reply_rules = ReplyRuleService(self.operations_database)
        self.reply_cycles = ReplyCycleService(self.operations_database)
        self.reply_intelligence = reply_intelligence or self._build_reply_intelligence()
        self.action_workflows = ActionWorkflowService(self.operations_database)
        self.tasks = TaskService(
            self._store,
            platform_resolver=self._resolve_account_platform,
        )
        self.tasks.recover_interrupted()
        self.publish_workflows = PublishWorkflowService(
            self._store,
            operations_database=self.operations_database,
            operation_events=self.operation_events,
            published_posts=self.published_posts,
        )
        self.approvals = ApprovalService(self._store)
        self.passive_replies = PassiveReplyService(
            self.inbound_events,
            self.tasks,
            self.approvals,
        )
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
            "runtime_instance_id": self._runtime_instance_id,
        }

    def list_capabilities(self) -> dict:
        capabilities = [item.to_dict() for item in list_capability_policies()]
        commands = [str(item["command"]) for item in capabilities]
        return {
            "success": True,
            "capabilities": capabilities,
            "platforms": list_platform_specs(),
            "platform_capabilities": {
                platform["platform_id"]: list_platform_capabilities(
                    str(platform["platform_id"]), commands
                )
                for platform in list_platform_specs()
            },
            "summary": {
                "total": len(capabilities),
                "enabled_in_v1": sum(bool(item["enabled_in_v1"]) for item in capabilities),
                "schedulable": sum(bool(item["supports_scheduling"]) for item in capabilities),
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
        platform: str = "xiaohongshu",
        bridge_port: int | None = None,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "创建账号槽位需要明确确认", 409)
        self.require_enabled_capability("account-add")
        self._ensure_slot_capacity()
        try:
            config = self._account_creator(
                name,
                platform=normalize_platform(platform),
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
        profile_display_name: str = "",
        confirmed: bool,
        platform: str = "xiaohongshu",
        bridge_port: int | None = None,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "绑定 Chrome Profile 需要明确确认", 409)
        self.require_enabled_capability("account-import")
        self._ensure_slot_capacity()
        try:
            config = self._profile_importer(
                name,
                platform=normalize_platform(platform),
                user_data_dir=user_data_dir,
                profile_directory=profile_directory,
                profile_display_name=profile_display_name,
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

        account_tasks = [task for task in self.tasks.list() if task.get("account_slot") == account]
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
        platform: str | None = None,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "创建配对请求需要明确确认", 409)
        self.require_enabled_capability("account-pair-begin")
        config = self._load_account(account)
        try:
            selected_platform = normalize_platform(platform or config.platform)
            if selected_platform != config.platform:
                identity = self._identity_loader(account) or {}
                if config.extension_instance_id or (identity.get("current") or {}).get("user_id"):
                    raise ValueError(
                        "已配对或已确认身份的账号平台已固定，请为其他平台创建独立账号槽位"
                    )
                running = self._bridge_status_reader(config).get("bridge_running")
                if running:
                    self._bridge_stopper(config)
                config = set_unpaired_account_platform(account, selected_platform)
            if platform is not None:
                self._pairing_bridge_starter(config)
            pairing = self._pairing_creator(config, ttl_seconds=ttl_seconds)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("PAIRING_FAILED", str(exc), 409) from exc
        return {"success": True, "pairing": pairing}

    def account_pairing_status(self, account: str) -> dict:
        try:
            status = self._pairing_status_reader(account)
        except (FileNotFoundError, ValueError) as exc:
            raise ServiceError("ACCOUNT_NOT_FOUND", str(exc), 404) from exc
        return {"success": True, "pairing": status}

    def begin_account_setup(
        self,
        account: str,
        *,
        confirmed: bool,
        ttl_seconds: int = 300,
    ) -> dict:
        """Start or resume the shortest safe path from one slot to READY."""
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "开始账号配置需要明确确认", 409)
        self.require_enabled_capability("account-start")
        config = self._load_account(account)
        pairing_status = self._pairing_status_reader(account)

        if pairing_status["paired"]:
            try:
                lifecycle = self._bridge_starter(config)
            except Exception as exc:
                raise ServiceError("ACCOUNT_SETUP_FAILED", str(exc), 409) from exc
            return {
                "success": True,
                "setup": {
                    "account": account,
                    "phase": "CONNECTION_READY" if lifecycle.get("ready") else "WAITING_EXTENSION",
                    "pairing": pairing_status,
                    "lifecycle": lifecycle,
                    "profile_mode": config.profile_mode,
                },
            }

        self.require_enabled_capability("account-pair-begin")
        try:
            bridge = self._pairing_bridge_starter(config)
            pairing = self._pairing_creator(config, ttl_seconds=ttl_seconds)
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("ACCOUNT_SETUP_FAILED", str(exc), 409) from exc
        return {
            "success": True,
            "setup": {
                "account": account,
                "phase": "WAITING_PAIRING",
                "pairing": pairing,
                "bridge": bridge,
                "profile_mode": config.profile_mode,
                "message": (
                    "Bridge 已启动，配对信息已准备；请在已经打开的目标 Profile 扩展中确认"
                    if config.profile_mode == "existing"
                    else "Bridge 已启动，配对信息已准备；请打开新 Profile 并在扩展中确认"
                ),
            },
        }

    def check_account_identity(self, account: str) -> dict:
        self.require_enabled_capability("account-identity", operation="check")
        config = self._load_account(account)
        page = self._page_for_account(config)
        try:
            identity = self._observe_identity(page)
        except ServiceError as exc:
            if exc.code == "LOGIN_REQUIRED":
                self._remember_runtime_logged_out(account)
            else:
                self._clear_runtime_identity_check(account)
            raise
        self._remember_runtime_identity(account, identity)
        return {"success": True, "account": account, "identity": identity}

    def record_account_identity(
        self,
        account: str,
        *,
        confirmed: bool,
        label: str = "",
        expected_user_id: str | None = None,
    ) -> dict:
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
        if expected_user_id is not None:
            if not expected_user_id.strip() or observed["user_id"] != expected_user_id.strip():
                raise ServiceError(
                    "IDENTITY_CHANGED",
                    "当前登录 UID 与确认页面不一致，请重新检查身份后确认",
                    409,
                )
            recorded = (self._identity_loader(account) or {}).get("current") or {}
            if recorded.get("user_id") and recorded["user_id"] != observed["user_id"]:
                raise ServiceError(
                    "IDENTITY_ALREADY_BOUND",
                    "槽位已绑定其他 UID，请使用换号流程完成核验",
                    409,
                )
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

    def replace_account_identity(
        self,
        account: str,
        *,
        confirmed: bool,
        expected_recorded_user_id: str,
        expected_observed_user_id: str,
        label: str = "",
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "覆盖槽位 UID 需要明确确认", 409)
        self.require_enabled_capability("account-identity", operation="record")
        if self._switch_loader(account):
            raise ServiceError(
                "ACCOUNT_SWITCH_PENDING",
                "账号正在换号，不能覆盖 UID；请先完成或取消当前换号流程",
                409,
            )
        observed = self.check_account_identity(account)["identity"]
        try:
            event = self._identity_replacer(
                account,
                observed,
                expected_recorded_user_id=expected_recorded_user_id,
                expected_observed_user_id=expected_observed_user_id,
                source="webui-identity-replace",
                label=label,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise ServiceError("IDENTITY_REPLACE_FAILED", str(exc), 409) from exc
        self._remember_runtime_identity(account, observed)
        return {
            "success": True,
            "account": account,
            "identity": event,
            "message": "槽位已改用当前登录账号；Profile 和槽位绑定保持不变",
        }

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
        self._remember_runtime_logged_out(account, observed_after, event="LOGOUT_CONFIRMED")
        next_action = "请在绑定的 Chrome Profile 中手动登录账号，登录后完成身份核验"
        return {
            "success": True,
            "account": account,
            "logged_out": logged_out,
            "verified_logged_out": True,
            "auth_state": "LOGGED_OUT",
            "identity": observed_after,
            "message": "退出成功，当前无账号" if logged_out else "当前无账号",
            "next_action": next_action,
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
        self._remember_runtime_logged_out(account, observed_after, event="SWITCH_LOGOUT_CONFIRMED")
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

    def restart_local_service(self) -> dict:
        active = [
            item
            for item in self.tasks.list()
            if item.get("state") in {"RUNNING", "CANCEL_REQUESTED"}
        ]
        if active:
            raise ServiceError(
                "ACTIVE_TASKS",
                f"当前有 {len(active)} 个执行中任务，请先终止或等待任务结束",
                409,
            )
        if self._webui_restarter is None:
            raise ServiceError("RESTART_UNAVAILABLE", "当前启动方式不支持从 WebUI 重启服务", 409)
        lifecycle = self._webui_restarter()
        return {"success": True, "status": "restart_scheduled", "lifecycle": lifecycle}

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

    def start_account_bridge_only(self, account: str) -> dict:
        """Start only the local Bridge process without opening the bound Profile."""
        self.require_enabled_capability("account-start")
        config = self._load_account(account)
        try:
            lifecycle = self._pairing_bridge_starter(config)
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
        capability = str(values.get("capability") or "")
        parameters = values.get("parameters") or {}
        manager_workflow = values.get("source_type") == "manager-agent" and capability in {
            "fill-publish",
            "fill-publish-video",
            "follow-user",
        }
        if not manager_workflow:
            self._validate_task_parameters(capability, parameters)
        elif capability == "follow-user":
            if not parameters.get("user_id"):
                raise ServiceError("INVALID_REQUEST", "关注任务需要目标用户")
            values["operation"] = "execute"
        else:
            preview = dict(parameters.get("preview") or {})
            self.publish_workflows._validate_preview(capability, preview)
            active = [
                t
                for t in self.tasks.list()
                if t["account_slot"] == values["account_slot"]
                and t["capability"] in {"fill-publish", "fill-publish-video", "long-article"}
                and t["state"] not in {"SUCCESS", "FAILED", "CANCELLED"}
            ]
            if active:
                raise ServiceError("PUBLISH_WORKFLOW_ACTIVE", "账号存在未完成的发布任务", 409)
            values["parameters"] = {
                **parameters,
                "preview": preview,
                "workflow_type": "publish",
                "stage": "preparing",
            }
        config = (values.get("parameters") or {}).get("operations_config")
        if isinstance(config, dict) and not (values.get("parameters") or {}).get("daily_plan"):
            self._store.set_setting(f"operations_config:{values['account_slot']}", config)
        create = (
            self.tasks.create_workflow
            if manager_workflow and capability != "follow-user"
            else self.tasks.create
        )
        return {"success": True, "task": create(**values)}

    def _check_account_operations(
        self, account: str, capability: str, parameters: dict, *, account_state: str = ""
    ) -> None:
        config = parameters.get("operations_config") or self._store.get_setting(
            f"operations_config:{account}", {}
        )
        events = {
            e.get("task_id"): e
            for e in self._store.list("events")
            if e.get("account_slot") == account
        }
        history = [
            {**t, "result": (events.get(t["task_id"]) or {}).get("result") or {}}
            for t in self.tasks.list()
            if t.get("account_slot") == account
        ]
        check_operations_policy(
            config,
            capability,
            history,
            account_state=account_state,
            write_intent=(
                str(get_operation_policy(capability).risk_level) != "L0"
                and (capability != "home-engagement" or bool(parameters.get("like_count")))
            ),
        )

    def list_tasks(self) -> dict:
        return {"success": True, "tasks": self.tasks.list()}

    def get_task(self, task_id: str) -> dict:
        return {"success": True, "task": self.tasks.get(task_id)}

    def confirm_publish_task(
        self,
        task_id: str,
        *,
        account_slot: str,
        verified_uid: str,
        confirmed: bool,
        preview_confirmed: bool,
        preview_revision: str,
    ) -> dict:
        """Execute one prepared publish task after an explicit WebUI preview check."""

        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        if not confirmed or not preview_confirmed:
            raise ServiceError(
                "CONFIRMATION_REQUIRED",
                "请先确认已在绑定的 Chrome Profile 中核对真实发布预览",
                409,
            )
        self.require_enabled_capability("click-publish")
        waiting = self.publish_workflows.get(task_id, account=account_slot)
        if waiting["state"] != "WAITING_APPROVAL":
            raise ServiceError("INVALID_TASK_STATE", "只有待确认的发布任务可以继续", 409)
        if (waiting.get("parameters") or {}).get("stage") != "preview_ready":
            raise ServiceError("PREVIEW_NOT_READY", "发布任务尚未进入最终预览确认阶段", 409)
        self._validate_publish_confirmation_identity(account_slot, verified_uid)
        self._check_account_operations(
            account_slot, "click-publish", waiting.get("parameters") or {}
        )
        self.publish_workflows.tasks.update_parameters(
            task_id,
            verified_uid=verified_uid,
        )
        task = self.publish_workflows.prepare_publish(
            task_id,
            account=account_slot,
            confirmed=True,
            preview_revision=preview_revision,
        )
        preview = (task.get("parameters") or {}).get("preview") or {}
        try:
            result = self.runner.execute(
                account_slot,
                "click-publish",
                {"expected_title": str(preview.get("title") or "")},
            )
        except PublishError as exc:
            failed = self.publish_workflows.fail(task_id, exc)
            return {"success": True, "task": failed}
        except Exception as exc:
            failed = self.publish_workflows.fail(task_id, exc, result_unknown=True)
            return {"success": True, "task": failed}
        completed = self.publish_workflows.complete_publish(task_id, result)
        return {
            "success": True,
            "task": completed,
            "result": result,
            "status": completed["state"],
        }

    def save_publish_task_as_draft(
        self,
        task_id: str,
        *,
        account_slot: str,
        verified_uid: str,
        confirmed: bool,
        preview_revision: str,
    ) -> dict:
        """Save a prepared publish form to the platform draft box."""

        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "保存为草稿需要明确确认", 409)
        self.require_enabled_capability("save-draft")
        waiting = self.publish_workflows.get(task_id, account=account_slot)
        if waiting["state"] != "WAITING_APPROVAL":
            raise ServiceError("INVALID_TASK_STATE", "只有待确认的发布任务可以保存为草稿", 409)
        self._validate_publish_confirmation_identity(account_slot, verified_uid)
        self._check_account_operations(account_slot, "save-draft", waiting.get("parameters") or {})
        task = self.publish_workflows.prepare_publish(
            task_id,
            account=account_slot,
            confirmed=True,
            preview_revision=preview_revision,
            action="save-draft",
        )
        preview = (task.get("parameters") or {}).get("preview") or {}
        try:
            result = self.runner.execute(
                account_slot,
                "save-draft",
                {"expected_title": str(preview.get("title") or "")},
            )
        except PublishError as exc:
            failed = self.publish_workflows.fail(task_id, exc, published=False)
            return {"success": True, "task": failed}
        except Exception as exc:
            failed = self.publish_workflows.fail(
                task_id,
                exc,
                result_unknown=True,
                published=False,
            )
            return {"success": True, "task": failed}
        completed = self.publish_workflows.complete_saved_draft(task_id, result)
        return {
            "success": True,
            "task": completed,
            "result": result,
            "status": completed["state"],
        }

    def execute_task(self, task_id: str) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        task = self.tasks.get(task_id)
        if task["capability"] == "send-private-messages" and task.get("operation") not in {
            "recipient",
            "automatic_reply",
        }:
            raise ServiceError(
                "AGENT_CLI_ONLY",
                "私信批次只能由 Agent 通过 Python CLI 执行",
                409,
            )
        if task["state"] != "QUEUED":
            raise ServiceError("INVALID_TASK_STATE", "只有排队中的任务可以执行", 409)
        task_platform = normalize_platform(task.get("platform"))
        config_platform = self._resolve_account_platform(task["account_slot"])
        if task_platform != config_platform:
            blocked = self.tasks.transition(
                task_id,
                "BLOCKED",
                error_code="PLATFORM_MISMATCH",
                recommended_action="任务平台与账号槽位平台不一致，请重新创建任务",
            )
            self._record_task_event(blocked, self._task_target_key(blocked))
            return {"success": True, "task": blocked}
        support = get_platform_capability(task_platform, task["capability"])
        if support in {CapabilitySupport.UNKNOWN, CapabilitySupport.UNSUPPORTED}:
            blocked = self.tasks.transition(
                task_id,
                "BLOCKED",
                error_code="PLATFORM_CAPABILITY_UNAVAILABLE",
                recommended_action=(
                    f"{task['capability']} 在 {task_platform} 上当前状态为 {support.value}"
                ),
            )
            self._record_task_event(blocked, self._task_target_key(blocked))
            return {"success": True, "task": blocked}
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
        parameters["platform"] = task_platform
        memory_identity = (parameters.get("memory_context") or {}).get("identity") or {}
        live_uid = _execution_uid(status.get("identity") or {})
        if memory_identity.get("uid") and (
            memory_identity["uid"] != live_uid
            or memory_identity.get("platform") != task_platform
        ):
            task = self.tasks.transition(
                task_id, "BLOCKED", error_code="MEMORY_IDENTITY_MISMATCH",
                recommended_action="当前账号身份与任务记忆不一致，请核对账号后重新下发任务",
            )
            self._record_task_event(task, target_key)
            return {"success": True, "task": task}
        if task["capability"] == "random-comment" and live_uid and not memory_identity:
            _, context = self.reply_memory_context(
                {"event_id": task_id, "account_slot": task["account_slot"],
                 "platform": task_platform, "payload": {"content": parameters.get("keyword", "")}},
                live_uid,
            )
            from memory_context import ordered_context

            parameters["memory_context"] = ordered_context(context, {**task, "parameters": parameters})
        persona_snapshot = task.get("persona_snapshot")
        if isinstance(persona_snapshot, dict) and persona_snapshot:
            parameters["persona_context"] = dict(persona_snapshot)
        if task["capability"] in {
            "collect-note-comments",
            "collect-private-messages",
            "collect-operations-metrics",
        } and not parameters.get("owner_user_id"):
            parameters["owner_user_id"] = _execution_uid(status["identity"])
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
        try:
            from operations_policy import check_daily_context

            check_daily_context(
                parameters, account=task["account_slot"],
                current_uid=_execution_uid(status.get("identity") or {}),
            )
            self._check_account_operations(
                task["account_slot"],
                task["capability"],
                parameters,
                account_state=str(status.get("error_code") or status.get("status") or ""),
            )
        except ServiceError as exc:
            blocked = self.tasks.transition(
                task_id, "BLOCKED", error_code=exc.code, recommended_action=exc.message
            )
            self._record_task_event(blocked, target_key)
            return {"success": True, "task": blocked}
        claimed = self.tasks.claim_for_execution(task_id)
        if claimed["state"] == "BLOCKED":
            self._record_task_event(claimed, target_key)
            return {"success": True, "task": claimed}
        try:
            from xhs.progress import record_progress_with

            with record_progress_with(lambda event: self.tasks.record_progress(task_id, event)):
                result = self.runner.execute(task["account_slot"], task["capability"], parameters)
        except Exception as exc:
            terminated = self._finalize_requested_task_termination(task_id, target_key)
            if terminated is not None:
                return {"success": True, "task": terminated}
            result_unknown = getattr(exc, "result_unknown", task["risk_level"] == "L1")
            final = "RESULT_UNKNOWN" if result_unknown else "FAILED"
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
        terminated = self._finalize_requested_task_termination(task_id, target_key)
        if terminated is not None:
            return {"success": True, "task": terminated}
        if task["capability"] in {"fill-publish", "fill-publish-video"}:
            waiting = self.publish_workflows.wait_for_confirmation(
                task_id,
                summary="发布内容已填写，请核对真实预览后确认发布",
            )
            return {
                "success": True,
                "task": waiting,
                "result": {**result, "published": False},
                "status": "WAITING_APPROVAL",
                "error_code": "PUBLISH_CONFIRMATION_REQUIRED",
                "recommended_action": "核对员工机真实发布预览并确认后，经理端继续回收结果",
            }
        if task["capability"] == "collect-note-comments":
            collected = self.comment_collector.ingest(
                account_slot=task["account_slot"],
                platform=task["platform"],
                comments=list(result.get("comments") or []),
                cursor_value=str(result.get("cursor") or ""),
                last_seen_time=result.get("last_seen_time"),
                task_id=task_id,
                scope={"parameters": task.get("parameters") or {}},
                coverage=str(result.get("coverage") or "UNKNOWN"),
                failures=list(result.get("failures") or []),
            )
            result = {**result, "inbound_events": collected}
        if task["capability"] == "collect-private-messages":
            collected = self.private_message_collector.ingest(
                account_slot=task["account_slot"],
                platform=task["platform"],
                messages=list(result.get("messages") or []),
                conversation_snapshots=list(result.get("conversation_snapshots") or []),
                cursor_value=str(result.get("cursor") or ""),
                last_seen_time=result.get("last_seen_time"),
            )
            reconciled = [
                self._reconcile_delayed_private_message_outbound(item)
                for item in collected.get("codex_outbound_reconciliations") or []
            ]
            collected["reconciled_outbound"] = reconciled
            result = {**result, "inbound_events": collected}
        if task["capability"] == "collect-operations-metrics":
            snapshots = self._record_metric_collection(
                task["account_slot"],
                result,
                platform=task["platform"],
            )
            result = {**result, "snapshots": snapshots}
        missing_reply_readback = task.get("operation") == "automatic_reply" and not result.get(
            "readback"
        )
        result_unknown = missing_reply_readback or bool(result.get("result_unknown"))
        completed = self.tasks.transition(
            task_id,
            (
                "RESULT_UNKNOWN"
                if result_unknown
                else "PARTIAL_SUCCESS"
                if result.get("partial")
                else "SUCCESS"
            ),
            result_summary=(
                "已触发回复发送，但没有取得页面回读"
                if missing_reply_readback
                else self._result_summary(result)
            ),
            error_code=(
                "REPLY_READBACK_MISSING"
                if missing_reply_readback
                else "RESULT_UNKNOWN"
                if result_unknown
                else ""
            ),
            recommended_action=(
                "先检查平台当前会话或评论线程，不要直接重发" if result_unknown else ""
            ),
        )
        self._record_task_event(completed, target_key, result=result)
        return {"success": True, "task": completed, "result": result}

    def retry_task(self, task_id: str) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        task = self.tasks.get(task_id)
        if task["capability"] == "send-private-messages":
            raise ServiceError(
                "AGENT_CLI_ONLY",
                "私信批次不能从 WebUI 重试，请由 Agent 查看逐条结果",
                409,
            )
        if task["state"] != "BLOCKED":
            raise ServiceError("INVALID_TASK_STATE", "只有需处理的任务可以重试", 409)
        self.tasks.transition(task_id, "QUEUED")
        return self.execute_task(task_id)

    def cancel_task(self, task_id: str) -> dict:
        task = self.tasks.get(task_id)
        if task["state"] == "RUNNING":
            self.tasks.transition(
                task_id,
                "CANCEL_REQUESTED",
                result_summary="用户已请求终止当前任务",
                recommended_action="正在停止该账号的自动化连接",
            )
            bridge_stopped = False
            bridge_stop_error = ""
            try:
                config = self._load_account(task["account_slot"])
                self._bridge_stopper(config)
                bridge_stopped = True
            except Exception as exc:
                bridge_stop_error = str(exc)
            return {
                "success": True,
                "task": self.tasks.get(task_id),
                "termination_requested": True,
                "bridge_stopped": bridge_stopped,
                "bridge_stop_error": bridge_stop_error,
                "message": (
                    "终止请求已发出，账号自动化连接已停止"
                    if bridge_stopped
                    else "终止请求已记录，正在等待当前步骤停止"
                ),
            }
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

    def _finalize_requested_task_termination(
        self,
        task_id: str,
        target_key: str,
    ) -> dict | None:
        current = self.tasks.get(task_id)
        if current["state"] != "CANCEL_REQUESTED":
            return None
        policy = get_operation_policy(current["capability"], current.get("operation"))
        final_state = "RESULT_UNKNOWN" if policy.requires_result_verification else "CANCELLED"
        completed = self.tasks.transition(
            task_id,
            final_state,
            result_summary=(
                "任务终止后，平台操作结果需要回读确认"
                if final_state == "RESULT_UNKNOWN"
                else "任务已由用户终止"
            ),
            error_code=(
                "TASK_TERMINATED_RESULT_UNKNOWN" if final_state == "RESULT_UNKNOWN" else ""
            ),
            recommended_action=(
                "先检查平台当前状态，不要直接重试" if final_state == "RESULT_UNKNOWN" else ""
            ),
        )
        self._record_task_event(completed, target_key)
        return completed

    def list_records(self) -> dict:
        records = sorted(
            self._store.list("events"),
            key=lambda item: item.get("finished_at") or "",
            reverse=True,
        )
        return {"success": True, "records": records}

    def list_inbound_events(self, **filters) -> dict:
        return {"success": True, "events": self.inbound_events.list(**filters)}

    def get_inbound_event(self, event_id: str) -> dict:
        return {"success": True, "event": self.inbound_events.get(event_id)}

    def collect_reply_inbox(
        self,
        account_slot: str,
        *,
        channels: list[str] | None = None,
        max_notes: int = 20,
        max_conversations: int = 20,
        limit: int = 20,
    ) -> dict:
        """Collect inbound reply events without generating or sending a reply."""

        self.require_enabled_capability("reply-inbox", operation="collect")
        selected_channels = self._normalize_reply_channels(channels)
        collection_results: list[dict] = []
        for channel in selected_channels:
            capability = (
                "collect-note-comments" if channel == "comment" else "collect-private-messages"
            )
            parameters = (
                {"max_notes": max(1, min(int(max_notes), 50))}
                if channel == "comment"
                else {
                    "max_conversations": max(1, min(int(max_conversations), 50)),
                    "context_limit": 20,
                }
            )
            task = self.tasks.create(
                source="codex",
                account_slot=account_slot,
                capability=capability,
                request_summary=f"Codex 回复收件箱采集：{channel}",
                parameters=parameters,
            )
            collection_results.append(self.execute_task(task["task_id"]))
        inbox = self.list_reply_inbox(
            account_slot,
            channels=selected_channels,
            handling_state="NEW",
            limit=limit,
        )
        return {
            "success": True,
            "collection_results": collection_results,
            "events": inbox["events"],
            "count": inbox["count"],
        }

    def list_reply_inbox(
        self,
        account_slot: str,
        *,
        channels: list[str] | None = None,
        handling_state: str = "NEW",
        limit: int = 20,
    ) -> dict:
        """Return a compact, stable event list for Codex triage."""

        self.require_enabled_capability("reply-inbox", operation="list")
        selected_channels = self._normalize_reply_channels(channels)
        event_types = {
            "comment": "note_comment",
            "private_message": "private_message",
        }
        allowed_types = {event_types[channel] for channel in selected_channels}
        events = [
            event
            for event in self.inbound_events.list(
                account_slot=account_slot,
                handling_state=handling_state,
                limit=max(1, min(int(limit), 100)),
            )
            if event.get("event_type") in allowed_types
            and not (handling_state == "NEW" and event.get("created_task_id"))
        ]
        compact = [self._compact_reply_event(event) for event in events]
        return {"success": True, "events": compact, "count": len(compact)}

    def get_reply_inbox_context(self, account_slot: str, event_id: str) -> dict:
        """Return the full context and account reply policy for one event."""

        self.require_enabled_capability("reply-inbox", operation="context")
        event = self._reply_event_for_account(account_slot, event_id)
        platform = self._resolve_account_platform(account_slot)
        rule = self.reply_rules.resolve(account_slot, platform=platform)
        recent_replies = [
            str(record.get("reply_text") or "")
            for record in self.reply_records.list(account_slot=account_slot, limit=20)
            if record.get("result_state") in {"SUCCESS", "RESULT_UNKNOWN"}
        ]
        payload = dict(event.get("payload") or {})
        conversation = None
        if event.get("event_type") == "private_message":
            conversation = self.private_message_conversations.get(
                account_slot,
                platform,
                str(event.get("object_id") or payload.get("user_id") or ""),
            )
        return {
            "success": True,
            "event": event,
            "channel": self._reply_event_channel(event),
            "policy": {
                "enabled": rule["enabled"],
                "comment_enabled": rule["comment_enabled"],
                "private_message_enabled": rule["private_message_enabled"],
                "content_scope": rule["content_scope"],
                "active_time_range": rule["active_time_range"],
                "hourly_limit": rule["hourly_limit"],
                "daily_limit": rule["daily_limit"],
                "auto_categories": rule["auto_categories"],
                "manual_categories": rule["manual_categories"],
                "reply_style": rule["reply_style"],
                "account_profile": rule["account_profile"],
                "knowledge": rule["knowledge"],
            },
            "recent_replies": recent_replies,
            "conversation_ownership": conversation,
            "context_guard": {
                "event_id": event["event_id"],
                "platform_event_id": event["platform_event_id"],
                "occurred_at": event["occurred_at"],
                "expected_peer_turn": list(payload.get("peer_turn") or []),
            },
        }

    def apply_codex_reply_decision(
        self,
        account_slot: str,
        decision_data: dict,
        *,
        source: str = "codex",
    ) -> dict:
        """Apply one Codex-authored reply, pending decision, or skip decision."""

        self.require_enabled_capability("reply-inbox", operation="apply")
        event_id = str(decision_data.get("event_id") or "").strip()
        action = str(decision_data.get("action") or "").strip().lower()
        if not event_id or action not in {"reply", "pending", "skip"}:
            raise ServiceError(
                "INVALID_REQUEST",
                "Codex 回复决策必须包含 event_id 和 reply、pending 或 skip 动作",
            )
        event = self._reply_event_for_account(account_slot, event_id)
        platform = self._resolve_account_platform(account_slot)
        if event.get("event_type") == "private_message":
            payload = dict(event.get("payload") or {})
            conversation = self.private_message_conversations.get(
                account_slot,
                platform,
                str(event.get("object_id") or payload.get("user_id") or ""),
            )
            if conversation and conversation.get("owner_state") == OWNER_HUMAN:
                raise ServiceError(
                    "CONVERSATION_OWNED_BY_HUMAN",
                    "该私信会话已由人工接管，请先确认恢复给 Codex",
                    409,
                )
        linked_task_id = str(event.get("created_task_id") or "")
        if event.get("handling_state") == "TASK_CREATED" and linked_task_id:
            linked_task = self.tasks.get(linked_task_id)
            if linked_task.get("state") == "FAILED":
                event = self.inbound_events.reopen_after_failed_task(
                    event_id,
                    linked_task_id,
                )
        if event.get("handling_state") != "NEW" or event.get("created_task_id"):
            raise ServiceError("EVENT_ALREADY_HANDLED", "该回复事件已经处理", 409)
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)

        rule = self.reply_rules.resolve(account_slot, platform=platform)
        if not rule["enabled"]:
            raise ServiceError("REPLY_RULE_DISABLED", "该账号的自动回复规则尚未启用", 409)
        status = self.get_account_status(account_slot)
        if not status["ready"]:
            raise ServiceError(
                "ACCOUNT_NOT_READY",
                status.get("next_action") or "账号未就绪",
                409,
            )
        verified_uid = _execution_uid(status.get("identity") or {})
        if not verified_uid:
            raise ServiceError("IDENTITY_UID_UNAVAILABLE", "当前账号缺少已核验 UID", 409)

        reply = str(decision_data.get("reply") or "").strip()
        if action == "reply" and not reply:
            raise ServiceError("INVALID_REQUEST", "reply 动作必须提供回复文本")
        recent_replies = [
            str(record.get("reply_text") or "")
            for record in self.reply_records.list(account_slot=account_slot, limit=20)
            if record.get("result_state") in {"SUCCESS", "RESULT_UNKNOWN"}
        ]
        try:
            confidence = float(decision_data.get("confidence") or 0)
        except (TypeError, ValueError) as exc:
            raise ServiceError("INVALID_REQUEST", "confidence 必须是数字") from exc
        generation = {
            "reply": reply,
            "intent": str(decision_data.get("intent") or "other").strip().lower(),
            "confidence": min(1.0, max(0.0, confidence)),
            "reason": str(decision_data.get("reason") or "codex_decision").strip(),
            "facts_used": self._reply_string_list(decision_data.get("facts_used")),
            "missing_information": self._reply_string_list(
                decision_data.get("missing_information")
            ),
            "quality_flags": ReplyIntelligenceService.quality_flags(reply, recent_replies),
            "manual_review_required": False,
            "mode": "codex",
            "model": "codex",
        }
        cycle = self.reply_cycles.start(
            account_slot=account_slot,
            platform=platform,
            source=source,
        )
        try:
            result = self._apply_codex_reply_action(
                cycle_id=cycle["cycle_id"],
                event=event,
                rule=rule,
                action=action,
                generation=generation,
                verified_uid=verified_uid,
                source=source,
            )
            task = result.get("task") or {}
            result_state = str(task.get("state") or "SUCCESS")
            finished = self.reply_cycles.finish(
                cycle["cycle_id"],
                result_state=result_state,
                summary={
                    "source": "codex",
                    "processed": 1,
                    "decision": result["decision"]["decision"],
                },
            )
            return {"success": True, "cycle": finished, **result}
        except Exception as exc:
            self.reply_cycles.finish(
                cycle["cycle_id"],
                result_state="FAILED",
                summary={"source": "codex", "processed": 0, "error": str(exc)},
            )
            raise

    def create_passive_reply_draft(
        self,
        event_id: str,
        *,
        verified_uid: str,
        content: str,
    ) -> dict:
        result = self.passive_replies.create_draft(
            event_id,
            verified_uid=verified_uid,
            content=content,
        )
        return {"success": True, **result}

    def intelligent_reply_status(self) -> dict:
        status = self.reply_intelligence.status()
        saved = self._store.get_setting("reply_model", {}) or {}
        return {
            "success": True,
            **status,
            "api_key_saved": bool(saved.get("api_key")),
            "configuration_source": (
                "webui" if saved else "environment" if status.get("configured") else "none"
            ),
        }

    def update_reply_model_settings(
        self,
        *,
        confirmed: bool,
        api_key: str = "",
        base_url: str,
        model: str,
        timeout_seconds: float = 60,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "保存模型配置需要明确确认", 409)
        normalized_url = base_url.strip().rstrip("/")
        normalized_model = model.strip()
        if not normalized_url.startswith(("https://", "http://")):
            raise ServiceError("INVALID_REQUEST", "模型 API 地址必须以 http:// 或 https:// 开头")
        if not normalized_model:
            raise ServiceError("INVALID_REQUEST", "模型名称不能为空")
        try:
            normalized_timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise ServiceError("INVALID_REQUEST", "模型超时时间必须是数字") from exc
        if not 1 <= normalized_timeout <= 300:
            raise ServiceError("INVALID_REQUEST", "模型超时时间必须在 1 到 300 秒之间")

        previous = self._store.get_setting("reply_model", {}) or {}
        normalized_key = api_key.strip() or str(previous.get("api_key") or "").strip()
        if not normalized_key and not os.getenv("XHS_REPLY_LLM_API_KEY", "").strip():
            raise ServiceError("INVALID_REQUEST", "首次保存必须填写 API Key")
        self._store.set_setting(
            "reply_model",
            {
                "api_key": normalized_key,
                "base_url": normalized_url,
                "model": normalized_model,
                "timeout_seconds": normalized_timeout,
            },
        )
        self.reply_intelligence = self._build_reply_intelligence()
        return self.intelligent_reply_status()

    def test_reply_model_connection(self) -> dict:
        return self.reply_intelligence.test_connection()

    def _build_reply_intelligence(self) -> ReplyIntelligenceService:
        saved = self._store.get_setting("reply_model", {}) or {}
        config = ReplyModelConfig.from_sources(saved)
        return ReplyIntelligenceService(OpenAICompatibleReplyClient(config))

    def create_intelligent_reply_draft(
        self,
        event_id: str,
        *,
        account_slot: str,
        verified_uid: str,
        account_profile: str = "",
        knowledge: str = "",
        reply_style: str = "natural",
    ) -> dict:
        event = self.inbound_events.get(event_id)
        if event["account_slot"] != account_slot:
            raise ServiceError("ACCOUNT_MISMATCH", "入站事件不属于当前账号槽位", 409)
        if not verified_uid.strip():
            raise ServiceError("INVALID_REQUEST", "生成回复草稿必须包含已核验 UID")
        if event.get("created_task_id"):
            existing = self.passive_replies.create_draft(
                event_id,
                verified_uid=verified_uid,
                content="",
            )
            generation = ((existing.get("draft") or {}).get("metadata") or {}).get(
                "intelligent_reply"
            )
            return {"success": True, "generation": generation, **existing}
        reply_action = (
            "send-private-messages"
            if event.get("event_type") == "private_message"
            else "reply-comment"
        )
        recent_replies = [
            str(draft.get("content") or "")
            for draft in sorted(
                self._store.list("drafts"),
                key=lambda item: item.get("updated_at") or "",
                reverse=True,
            )
            if draft.get("account_slot") == account_slot
            and draft.get("action_type") == reply_action
        ][:20]
        generation = self.generate_reply_with_memory(
            event, verified_uid,
            account_profile=account_profile,
            knowledge=knowledge,
            reply_style=reply_style,
            recent_replies=recent_replies,
        )
        result = self.passive_replies.create_draft(
            event_id,
            verified_uid=verified_uid,
            content=generation["reply"],
            generation=generation,
        )
        return {"success": True, "generation": generation, **result}

    def list_operation_events(self, **filters) -> dict:
        return {"success": True, "events": self.operation_events.list(**filters)}

    def data_recovery_summary(self) -> dict:
        return {"success": True, "summary": self.data_recovery.summary()}

    def list_published_posts(self, **filters) -> dict:
        return {"success": True, "posts": self.published_posts.list(**filters)}

    def get_published_post(self, published_post_id: str) -> dict:
        return {
            "success": True,
            "post": self.published_posts.get(published_post_id),
        }

    def list_operation_records(self, **filters) -> dict:
        return {"success": True, "records": self.operation_events.list(**filters)}

    def list_reply_records(self, **filters) -> dict:
        return {"success": True, "records": self.reply_records.list(**filters)}

    def get_reply_record(self, reply_record_id: str) -> dict:
        return {
            "success": True,
            "record": self.reply_records.get(reply_record_id),
        }

    def get_metric_history(
        self,
        account_slot: str,
        entity_type: str,
        entity_id: str,
        *,
        limit: int = 100,
    ) -> dict:
        return {
            "success": True,
            "snapshots": self.metrics.history(
                account_slot=account_slot,
                entity_type=entity_type,
                entity_id=entity_id,
                limit=limit,
            ),
        }

    def get_metric_delta(
        self,
        account_slot: str,
        entity_type: str,
        entity_id: str,
    ) -> dict:
        return {
            "success": True,
            "delta": self.metrics.latest_delta(
                account_slot=account_slot,
                entity_type=entity_type,
                entity_id=entity_id,
            ),
        }

    def generate_operation_report(self, **values) -> dict:
        if "platform" not in values and values.get("account_slot"):
            values["platform"] = self._resolve_account_platform(values["account_slot"])
        return {"success": True, "report": self.reports.generate(**values)}

    def list_operation_reports(self, **filters) -> dict:
        return {"success": True, "reports": self.reports.list(**filters)}

    def get_operation_report(self, report_id: str) -> dict:
        return {"success": True, "report": self.reports.get(report_id)}

    def revise_operation_report(self, report_id: str, **values) -> dict:
        return {
            "success": True,
            "report": self.reports.revise(report_id, **values),
        }

    def list_reply_rules(self, *, account_slot: str | None = None) -> dict:
        return {
            "success": True,
            "rules": self.reply_rules.list(account_slot=account_slot),
        }

    def create_reply_rule(self, **values) -> dict:
        if "platform" not in values and values.get("account_slot"):
            values["platform"] = self._resolve_account_platform(values["account_slot"])
        return {"success": True, "rule": self.reply_rules.create(**values)}

    def update_reply_rule(self, rule_id: str, **changes) -> dict:
        return {"success": True, "rule": self.reply_rules.update(rule_id, **changes)}

    def set_reply_rule_enabled(self, rule_id: str, *, enabled: bool) -> dict:
        return {
            "success": True,
            "rule": self.reply_rules.set_enabled(rule_id, enabled),
        }

    @staticmethod
    def _normalize_reply_channels(channels: list[str] | None) -> list[str]:
        requested = set(channels or ["comment", "private_message"])
        unknown = requested - {"comment", "private_message"}
        if unknown:
            raise ServiceError(
                "INVALID_REQUEST",
                f"未知回复渠道: {', '.join(sorted(unknown))}",
            )
        return [channel for channel in ("comment", "private_message") if channel in requested]

    @staticmethod
    def _reply_event_channel(event: dict) -> str:
        return "private_message" if event.get("event_type") == "private_message" else "comment"

    @staticmethod
    def _reply_string_list(value: object, *, limit: int = 20) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item).strip() for item in value[:limit] if str(item).strip()]

    @staticmethod
    def _compact_reply_event(event: dict) -> dict:
        payload = dict(event.get("payload") or {})
        return {
            "event_id": event.get("event_id"),
            "event_type": event.get("event_type"),
            "channel": ApplicationService._reply_event_channel(event),
            "handling_state": event.get("handling_state"),
            "occurred_at": event.get("occurred_at"),
            "actor_user_id": event.get("actor_user_id"),
            "object_id": event.get("object_id"),
            "nickname": str(payload.get("nickname") or ""),
            "content": str(payload.get("content") or ""),
            "note_title": str(payload.get("note_title") or ""),
        }

    def _reply_event_for_account(self, account_slot: str, event_id: str) -> dict:
        event = self.inbound_events.get(event_id)
        if event.get("account_slot") != account_slot:
            raise ServiceError("ACCOUNT_MISMATCH", "入站事件不属于当前账号槽位", 409)
        if event.get("event_type") not in {"note_comment", "private_message"}:
            raise ServiceError("INVALID_EVENT_TYPE", "该事件不能作为评论或私信回复处理", 409)
        return event

    def _apply_codex_reply_action(
        self,
        *,
        cycle_id: str,
        event: dict,
        rule: dict,
        action: str,
        generation: dict,
        verified_uid: str,
        source: str,
    ) -> dict:
        payload = dict(event.get("payload") or {})
        if (
            event.get("event_type") == "note_comment"
            and rule.get("content_scope") == "all_own_notes"
            and not payload.get("is_own_note")
        ):
            action = "skip"
            generation["reason"] = "outside_own_notes"

        if action in {"pending", "skip"}:
            reason = str(generation.get("reason") or f"codex_{action}")
            decision = self.reply_cycles.record_decision(
                cycle_id=cycle_id,
                event=event,
                decision=action,
                reason=reason,
                generation=generation,
                rule=rule,
            )
            if action == "skip":
                self.inbound_events.ignore(event["event_id"])
                self._mark_private_message_codex_handled(event)
            else:
                self.inbound_events.mark_handled(event["event_id"])
                self._handoff_private_message(event)
            return {"decision": decision}

        channel = self._reply_event_channel(event)
        rule_decision = self.reply_rules.decision(
            rule["rule_id"],
            classification=str(generation.get("intent") or "other"),
            channel=channel,
            quality_flags=list(generation.get("quality_flags") or []),
            facts_used=list(generation.get("facts_used") or []),
            missing_information=list(generation.get("missing_information") or []),
        )
        if not rule_decision["authorized"]:
            decision = self.reply_cycles.record_decision(
                cycle_id=cycle_id,
                event=event,
                decision=rule_decision["decision"],
                reason=rule_decision["reason"],
                generation=generation,
                rule=rule,
            )
            if decision["decision"] == "skip":
                self.inbound_events.ignore(event["event_id"])
                self._mark_private_message_codex_handled(event)
            else:
                self.inbound_events.mark_handled(event["event_id"])
                self._handoff_private_message(event)
            return {"decision": decision}

        execution = self._execute_automatic_reply(
            event=event,
            generation=generation,
            verified_uid=verified_uid,
            source=source,
            rule=rule,
        )
        task = execution["task"]
        result_state = str(task.get("state") or "FAILED")
        error_code = str(task.get("error_code") or "")
        final_decision = "reply" if result_state == "SUCCESS" else "pending"
        if error_code == "PRIVATE_MESSAGE_CONTEXT_CHANGED":
            decision_reason = "conversation_changed"
        elif result_state == "RESULT_UNKNOWN":
            decision_reason = "result_unknown"
        elif result_state == "SUCCESS":
            decision_reason = "sent_with_readback"
        else:
            decision_reason = "send_failed"
        decision = self.reply_cycles.record_decision(
            cycle_id=cycle_id,
            event=event,
            decision=final_decision,
            reason=decision_reason,
            generation=generation,
            rule=rule,
            reply_task_id=task["task_id"],
            result_state=task["state"],
        )
        if result_state == "SUCCESS" or error_code == "PRIVATE_MESSAGE_CONTEXT_CHANGED":
            self.inbound_events.mark_handled(event["event_id"])
        else:
            self.inbound_events.attach_task(event["event_id"], task["task_id"])
        if result_state == "SUCCESS":
            self._mark_private_message_codex_handled(event)
        elif error_code != "PRIVATE_MESSAGE_CONTEXT_CHANGED":
            self._handoff_private_message(event, owner_state="CODEX")
        return {
            "decision": decision,
            "task": task,
            "result": execution.get("result"),
        }

    def list_private_message_conversations(
        self,
        account_slot: str,
        *,
        owner_state: str | None = None,
        limit: int = 100,
    ) -> dict:
        return {
            "success": True,
            "conversations": self.private_message_conversations.list(
                account_slot=account_slot,
                owner_state=owner_state,
                limit=limit,
            ),
        }

    def resume_private_message_conversation(
        self,
        account_slot: str,
        user_id: str,
        *,
        confirmed: bool,
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "恢复 Codex 私信处理需要明确确认", 409)
        platform = self._resolve_account_platform(account_slot)
        conversation = self.private_message_conversations.resume(
            account_slot=account_slot,
            platform=platform,
            user_id=user_id,
        )
        return {"success": True, "conversation": conversation}

    def _handoff_private_message(
        self, event: dict, *, owner_state: str = OWNER_HUMAN
    ) -> dict | None:
        if event.get("event_type") != "private_message":
            return None
        payload = dict(event.get("payload") or {})
        return self.private_message_conversations.handoff(
            str(event.get("account_slot") or ""),
            str(event.get("platform") or "xiaohongshu"),
            str(event.get("object_id") or payload.get("user_id") or ""),
            owner_state=owner_state,
            event_id=str(event.get("event_id") or ""),
            message_count=int(payload.get("message_count") or 0),
        )

    def _mark_private_message_codex_handled(self, event: dict) -> dict | None:
        if event.get("event_type") != "private_message":
            return None
        payload = dict(event.get("payload") or {})
        return self.private_message_conversations.mark_codex_handled(
            account_slot=str(event.get("account_slot") or ""),
            platform=str(event.get("platform") or "xiaohongshu"),
            user_id=str(event.get("object_id") or payload.get("user_id") or ""),
            message_count=int(payload.get("message_count") or 0),
        )

    def _reconcile_delayed_private_message_outbound(self, item: dict) -> dict:
        task_id = str(item.get("reply_task_id") or "")
        event_id = str(item.get("inbound_event_id") or "")
        readback = {
            "source": "conversation_snapshot",
            "message_count": int(item.get("message_count") or 0),
            "content": str(item.get("content") or ""),
        }
        task = self.tasks.reconcile_result_unknown(
            task_id,
            result_summary="延迟回读确认私信已发送",
        )
        operation_event = self.operation_events.reconcile_result_unknown(
            task_id,
            readback=readback,
        )
        reply_record = self.reply_records.reconcile_result_unknown(
            task_id,
            readback=readback,
        )
        decision = self.reply_cycles.reconcile_result_unknown(
            inbound_event_id=event_id,
            reply_task_id=task_id,
        )
        event = self.inbound_events.mark_handled(event_id)
        return {
            "task": task,
            "operation_event": operation_event,
            "reply_record": reply_record,
            "decision": decision,
            "event": event,
        }

    def list_reply_cycles(self, *, account_slot: str | None = None) -> dict:
        return {
            "success": True,
            "cycles": self.reply_cycles.list_cycles(account_slot=account_slot),
        }

    def list_reply_decisions(
        self,
        *,
        account_slot: str | None = None,
        decision: str | None = None,
    ) -> dict:
        return {
            "success": True,
            "decisions": self.reply_cycles.list_decisions(
                account_slot=account_slot,
                decision=decision,
            ),
        }

    def run_reply_cycle(
        self,
        *,
        account_slot: str,
        source: str = "manual",
        channels: list[str] | None = None,
        max_events: int = 20,
        max_notes: int = 20,
        max_conversations: int = 20,
        inspection: bool = False,
    ) -> dict:
        """Collect and handle a bounded batch of new inbound reply events."""

        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        status = self.get_account_status(account_slot)
        if not status["ready"]:
            raise ServiceError(
                "ACCOUNT_NOT_READY",
                status.get("next_action") or "账号未就绪",
                409,
            )
        verified_uid = _execution_uid(status.get("identity") or {})
        if not verified_uid:
            raise ServiceError("IDENTITY_UID_UNAVAILABLE", "当前账号缺少已核验 UID", 409)
        platform = self._resolve_account_platform(account_slot)
        rule = self.reply_rules.resolve(account_slot, platform=platform)
        if not rule["enabled"] and not inspection:
            raise ServiceError("REPLY_RULE_DISABLED", "该账号的自动回复规则尚未启用", 409)

        requested_channels = set(channels or ["comment", "private_message"])
        unknown_channels = requested_channels - {"comment", "private_message"}
        if unknown_channels:
            raise ServiceError(
                "INVALID_REQUEST",
                f"未知回复渠道: {', '.join(sorted(unknown_channels))}",
            )
        selected_channels = [
            channel
            for channel in ("comment", "private_message")
            if channel in requested_channels and (inspection or rule[f"{channel}_enabled"])
        ]
        if not selected_channels:
            raise ServiceError("REPLY_CHANNEL_DISABLED", "本次选择的回复渠道均未启用", 409)

        cycle = self.reply_cycles.start(
            account_slot=account_slot,
            platform=platform,
            source=source,
        )
        collection_results: list[dict] = []
        decisions: list[dict] = []
        try:
            if inspection:
                task = self.tasks.create(
                    source=source,
                    account_slot=account_slot,
                    capability="collect-operations-metrics",
                    request_summary="员工巡检采集粉丝与点赞指标",
                    parameters={"max_notes": max_notes},
                )
                collection_results.append(self.execute_task(task["task_id"]))
            for channel in selected_channels:
                capability = (
                    "collect-note-comments" if channel == "comment" else "collect-private-messages"
                )
                parameters = (
                    {"max_notes": max(1, min(int(max_notes), 50))}
                    if channel == "comment"
                    else {
                        "max_conversations": max(
                            1,
                            min(int(max_conversations), 50),
                        ),
                        "context_limit": 20,
                    }
                )
                task = self.tasks.create(
                    source=source,
                    account_slot=account_slot,
                    capability=capability,
                    request_summary=f"自动回复周期采集：{channel}",
                    parameters=parameters,
                )
                collection_results.append(self.execute_task(task["task_id"]))

            allowed_event_types = {
                "comment": "note_comment",
                "private_message": "private_message",
            }
            event_types = {
                allowed_event_types[channel] for channel in selected_channels
                if rule["enabled"] and rule[f"{channel}_enabled"]
            }
            events = [
                event
                for event in self.inbound_events.list(
                    account_slot=account_slot,
                    handling_state="NEW",
                    limit=max(1, min(int(max_events), 100)),
                )
                if event.get("event_type") in event_types and not event.get("created_task_id")
            ]
            for event in reversed(events):
                decisions.append(
                    self._handle_automatic_reply_event(
                        cycle_id=cycle["cycle_id"],
                        event=event,
                        rule=rule,
                        verified_uid=verified_uid,
                        source=source,
                    )
                )

            counts = {
                value: sum(item.get("decision") == value for item in decisions)
                for value in ("reply", "pending", "skip")
            }
            collection_failed = any(
                item["task"]["state"] not in {"SUCCESS", "PARTIAL_SUCCESS"}
                for item in collection_results
            )
            summary = {
                "channels": selected_channels,
                "collected": sum(
                    int((item.get("result") or {}).get("count") or 0) for item in collection_results
                ),
                "processed": len(decisions),
                **counts,
            }
            finished = self.reply_cycles.finish(
                cycle["cycle_id"],
                result_state="PARTIAL_SUCCESS" if collection_failed else "SUCCESS",
                summary=summary,
            )
            from polling_receipt import cycle_observations

            reply_task_ids = {item.get("reply_task_id") for item in decisions}
            reply_records = [
                item for item in self.reply_records.list(account_slot=account_slot)
                if item.get("reply_task_id") in reply_task_ids
            ]
            return {
                "success": True,
                "state": finished["result_state"],
                "incremental_data": cycle_observations(
                    finished, collection_results, reply_records
                ),
                "cycle": finished,
                "collection_results": collection_results,
                "decisions": decisions,
            }
        except Exception as exc:
            self.reply_cycles.finish(
                cycle["cycle_id"],
                result_state="FAILED",
                summary={"error": str(exc), "processed": len(decisions)},
            )
            raise

    def _handle_automatic_reply_event(
        self,
        *,
        cycle_id: str,
        event: dict,
        rule: dict,
        verified_uid: str,
        source: str,
    ) -> dict:
        payload = dict(event.get("payload") or {})
        if (
            event.get("event_type") == "note_comment"
            and rule.get("content_scope") == "all_own_notes"
            and not payload.get("is_own_note")
        ):
            decision = self.reply_cycles.record_decision(
                cycle_id=cycle_id,
                event=event,
                decision="skip",
                reason="outside_own_notes",
                rule=rule,
            )
            self.inbound_events.ignore(event["event_id"])
            return decision
        recent_replies = [
            str(record.get("reply_text") or "")
            for record in self.reply_records.list(
                account_slot=event["account_slot"],
                limit=20,
            )
            if record.get("result_state") in {"SUCCESS", "RESULT_UNKNOWN"}
        ]
        try:
            generation = self.generate_reply_with_memory(
                event, verified_uid,
                account_profile=str(rule.get("account_profile") or ""),
                knowledge=str(rule.get("knowledge") or ""),
                reply_style=str(rule.get("reply_style") or "natural"),
                recent_replies=recent_replies,
                mode="automatic",
            )
        except Exception as exc:
            decision = self.reply_cycles.record_decision(
                cycle_id=cycle_id,
                event=event,
                decision="pending",
                reason=(exc.code if isinstance(exc, ServiceError) else "generation_failed"),
                rule=rule,
            )
            self.inbound_events.mark_handled(event["event_id"])
            self._handoff_private_message(event)
            return decision
        channel = "private_message" if event.get("event_type") == "private_message" else "comment"
        rule_decision = self.reply_rules.decision(
            rule["rule_id"],
            classification=str(generation.get("intent") or "other"),
            channel=channel,
            quality_flags=list(generation.get("quality_flags") or []),
            facts_used=list(generation.get("facts_used") or []),
            missing_information=list(generation.get("missing_information") or []),
        )
        if not rule_decision["authorized"]:
            decision = self.reply_cycles.record_decision(
                cycle_id=cycle_id,
                event=event,
                decision=rule_decision["decision"],
                reason=rule_decision["reason"],
                generation=generation,
                rule=rule,
            )
            if decision["decision"] == "skip":
                self.inbound_events.ignore(event["event_id"])
                self._mark_private_message_codex_handled(event)
            else:
                self.inbound_events.mark_handled(event["event_id"])
                self._handoff_private_message(event)
            return decision

        execution = self._execute_automatic_reply(
            event=event,
            generation=generation,
            verified_uid=verified_uid,
            source=source,
            rule=rule,
        )
        task = execution["task"]
        result_state = str(task.get("state") or "FAILED")
        error_code = str(task.get("error_code") or "")
        final_decision = "reply" if result_state == "SUCCESS" else "pending"
        if error_code == "PRIVATE_MESSAGE_CONTEXT_CHANGED":
            decision_reason = "conversation_changed"
        elif result_state == "RESULT_UNKNOWN":
            decision_reason = "result_unknown"
        elif result_state == "SUCCESS":
            decision_reason = "sent_with_readback"
        else:
            decision_reason = "send_failed"
        decision = self.reply_cycles.record_decision(
            cycle_id=cycle_id,
            event=event,
            decision=final_decision,
            reason=decision_reason,
            generation=generation,
            rule=rule,
            reply_task_id=task["task_id"],
            result_state=task["state"],
        )
        if result_state == "SUCCESS" or error_code == "PRIVATE_MESSAGE_CONTEXT_CHANGED":
            self.inbound_events.mark_handled(event["event_id"])
        else:
            self.inbound_events.attach_task(event["event_id"], task["task_id"])
        if result_state == "SUCCESS":
            self._mark_private_message_codex_handled(event)
        elif error_code != "PRIVATE_MESSAGE_CONTEXT_CHANGED":
            self._handoff_private_message(event, owner_state="CODEX")
        return decision

    def _execute_automatic_reply(
        self,
        *,
        event: dict,
        generation: dict,
        verified_uid: str,
        source: str,
        rule: dict,
    ) -> dict:
        payload = dict(event.get("payload") or {})
        private_message = event.get("event_type") == "private_message"
        capability = "send-private-messages" if private_message else "reply-comment"
        target_id = str(
            payload.get("user_id")
            if private_message
            else payload.get("notification_id")
            or payload.get("comment_id")
            or event.get("platform_event_id")
            or ""
        )
        parameters = {
            "feed_id": "" if private_message else str(payload.get("feed_id") or ""),
            "xsec_token": "" if private_message else str(payload.get("xsec_token") or ""),
            "comment_id": "" if private_message else str(payload.get("comment_id") or ""),
            "notification_id": (
                "" if private_message else str(payload.get("notification_id") or "")
            ),
            "user_id": str(payload.get("user_id") or event.get("actor_user_id") or ""),
            "nickname": str(payload.get("nickname") or ""),
            "original_content": str(payload.get("content") or ""),
            "content": str(generation.get("reply") or ""),
            "verified_uid": verified_uid,
            "rule_id": str(rule.get("rule_id") or ""),
            "rule_version": int(rule.get("rule_version") or 0),
        }
        if private_message:
            parameters["expected_peer_turn"] = list(
                payload.get("peer_turn") or [payload.get("content") or ""]
            )
        task = self.tasks.create(
            source=source,
            source_type="platform_event",
            source_event_id=event["event_id"],
            account_slot=event["account_slot"],
            platform=event.get("platform"),
            capability=capability,
            operation="automatic_reply",
            request_summary=("自动回复新私信" if private_message else "自动回复新评论"),
            target_type="user" if private_message else "comment",
            target_id=target_id,
            parameters=parameters,
        )
        execution = self.execute_task(task["task_id"])
        completed = execution["task"]
        operation_event = self.operation_events.get_by_task(completed["task_id"])
        draft = {
            "verified_uid": verified_uid,
            "content": generation["reply"],
            "action_type": capability,
        }
        self.reply_records.record(
            completed,
            draft=draft,
            source_event=event,
            result=execution.get("result"),
            operation_event_id=str(operation_event.get("operation_event_id") or ""),
        )
        return execution

    def list_action_drafts(self, *, account_slot: str | None = None) -> dict:
        return {
            "success": True,
            "drafts": self.action_workflows.list_drafts(account_slot=account_slot),
        }

    def create_action_draft(self, **values) -> dict:
        if values.get("action_type") in {"follow-user", "send-private-message"}:
            raise ServiceError(
                "AGENT_CLI_ONLY",
                "该动作只能由 Agent 通过 Python CLI 创建",
                409,
            )
        if "platform" not in values and values.get("account_slot"):
            values["platform"] = self._resolve_account_platform(values["account_slot"])
        return {"success": True, "draft": self.action_workflows.create_draft(**values)}

    def update_action_draft(self, draft_id: str, **changes) -> dict:
        return {
            "success": True,
            "draft": self.action_workflows.update_draft(draft_id, **changes),
        }

    def confirm_action_draft(self, draft_id: str, *, ttl_seconds: int = 300) -> dict:
        return {
            "success": True,
            "approval": self.action_workflows.confirm(
                draft_id,
                ttl_seconds=ttl_seconds,
            ),
        }

    def prepare_follow_user(
        self,
        account_slot: str,
        *,
        user_id: str,
        xsec_token: str,
    ) -> dict:
        """Read one explicit target and create an Agent-owned confirmation draft."""

        self.require_enabled_capability("follow-user-preview")
        user_id = str(user_id or "").strip()
        xsec_token = str(xsec_token or "").strip()
        if not user_id or not xsec_token:
            raise ServiceError("INVALID_REQUEST", "主加预览必须包含用户 ID 和 XSEC Token")
        task = self.tasks.create(
            source="agent",
            account_slot=account_slot,
            capability="follow-user-preview",
            request_summary=f"读取主加目标：{user_id}",
            target_type="user",
            target_id=user_id,
            parameters={"user_id": user_id, "xsec_token": xsec_token},
        )
        preview_result = self.execute_task(task["task_id"])
        if preview_result["task"]["state"] != "SUCCESS":
            raise ServiceError(
                "FOLLOW_PREVIEW_FAILED",
                preview_result["task"].get("result_summary") or "无法读取目标博主主页",
                409,
            )
        target = preview_result.get("result") or {}
        status = self.get_account_status(account_slot)
        live_uid = str(
            (status.get("identity") or {}).get("execution_user_id")
            or (status.get("identity") or {}).get("live_user_id")
            or (status.get("identity") or {}).get("user_id")
            or ""
        )
        if not status.get("ready") or not live_uid:
            raise ServiceError(
                "ACCOUNT_NOT_READY",
                status.get("next_action") or "目标账号尚未完成身份核验",
                409,
            )
        draft = self.action_workflows.create_draft(
            account_slot=account_slot,
            platform=self._resolve_account_platform(account_slot),
            verified_uid=live_uid,
            action_type="follow-user",
            target_id=user_id,
            payload={
                "target_nickname": target.get("nickname") or "",
                "target_red_id": target.get("red_id") or "",
                "target_description": target.get("description") or "",
                "current_button_text": target.get("button_text") or "",
                "already_following": bool(target.get("following")),
            },
        )
        return {
            "success": True,
            "task": preview_result["task"],
            "draft": draft,
            "preview": draft["preview"],
            "message": (
                "目标已经关注，无需重复执行"
                if draft["preview"]["already_following"]
                else "目标已读取，可由 Agent 直接执行关注"
            ),
        }

    def follow_user_direct(
        self,
        account_slot: str,
        *,
        user_id: str,
        xsec_token: str,
    ) -> dict:
        """Preview and follow one target directly without user approval."""

        prepared = self.prepare_follow_user(
            account_slot,
            user_id=user_id,
            xsec_token=xsec_token,
        )
        execution = self.execute_follow_user(
            account_slot,
            draft_id=prepared["draft"]["draft_id"],
            xsec_token=xsec_token,
            confirmed=True,
        )
        return {**execution, "preview": prepared["preview"]}

    def execute_follow_user(
        self,
        account_slot: str,
        *,
        draft_id: str,
        xsec_token: str,
        confirmed: bool,
    ) -> dict:
        """Consume one target preview and execute one idempotent follow action."""

        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "主加必须明确确认目标博主", 409)
        self.require_enabled_capability("follow-user")
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        draft = self.action_workflows.get_draft(str(draft_id or "").strip())
        if draft["action_type"] != "follow-user" or draft["account_slot"] != account_slot:
            raise ServiceError("CONFIRMATION_MISMATCH", "主加草稿与目标账号不匹配", 409)
        if draft["state"] != "DRAFT":
            raise ServiceError("CONFIRMATION_CONSUMED", "该主加草稿已经确认或执行", 409)
        xsec_token = str(xsec_token or "").strip()
        if not xsec_token:
            raise ServiceError("INVALID_REQUEST", "执行主加必须包含 XSEC Token")

        status = self.get_account_status(account_slot)
        identity = status.get("identity") or {}
        live_uid = _execution_uid(identity) or str(identity.get("user_id") or "")
        if not status.get("ready") or live_uid != draft["verified_uid"]:
            raise ServiceError(
                "IDENTITY_MISMATCH",
                "当前登录账号与主加预览时核验的账号不一致",
                409,
            )
        approval = self.action_workflows.confirm(draft["draft_id"])
        self.action_workflows.consume(approval["approval_id"])
        task = self.tasks.create(
            source="agent",
            account_slot=account_slot,
            capability="follow-user",
            operation="execute",
            request_summary=(
                f"关注博主：{draft['preview'].get('target_nickname') or draft['target_id']}"
            ),
            target_type="user",
            target_id=draft["target_id"],
            parameters={
                "user_id": draft["target_id"],
                "xsec_token": xsec_token,
                "expected_nickname": draft["preview"].get("target_nickname") or "",
            },
        )
        execution = self.execute_task(task["task_id"])
        final_draft = self.action_workflows.mark_execution_result(
            draft["draft_id"], execution["task"]["state"]
        )
        return {**execution, "draft": final_draft}

    def get_private_message_context(
        self,
        account_slot: str,
        *,
        user_id: str,
        xsec_token: str = "",
        limit: int = 10,
    ) -> dict:
        """Read one recipient profile/conversation for Agent personalization."""

        self.require_enabled_capability("private-message-context")
        user_id = str(user_id or "").strip()
        if not user_id:
            raise ServiceError("INVALID_REQUEST", "私信上下文必须包含收件人 ID")
        task = self.tasks.create(
            source="agent",
            account_slot=account_slot,
            capability="private-message-context",
            request_summary=f"读取私信上下文：{user_id}",
            target_type="user",
            target_id=user_id,
            parameters={
                "user_id": user_id,
                "xsec_token": str(xsec_token or "").strip(),
                "limit": max(1, min(int(limit), 20)),
            },
        )
        return self.execute_task(task["task_id"])

    def collect_private_message_inbox(
        self,
        account_slot: str,
        *,
        max_conversations: int = 20,
    ) -> dict:
        """Collect latest incoming text from the visible private-message inbox."""

        self.require_enabled_capability("collect-private-messages")
        task = self.tasks.create(
            source="agent",
            account_slot=account_slot,
            capability="collect-private-messages",
            request_summary=f"读取最近 {max_conversations} 个私信会话",
            target_type="inbox",
            target_id="private-messages",
            parameters={"max_conversations": int(max_conversations), "context_limit": 10},
        )
        return self.execute_task(task["task_id"])

    def send_private_messages(
        self,
        account_slot: str,
        *,
        recipients: list[dict] | None = None,
        resume_task_id: str = "",
    ) -> dict:
        """Send one Codex-generated batch or resume its retryable recipients."""

        self.require_enabled_capability("send-private-messages")
        if recipients is not None and resume_task_id:
            raise ServiceError(
                "INVALID_REQUEST",
                "新私信收件人和恢复任务不能同时提供",
                409,
            )
        verified_uid = self._private_message_verified_uid(account_slot)
        previous_results: list[dict] = []
        source_task_id = ""
        if resume_task_id:
            source = self.tasks.get(resume_task_id)
            parameters = source.get("parameters") or {}
            if source["account_slot"] != account_slot:
                raise ServiceError("ACCOUNT_MISMATCH", "恢复任务与当前账号不匹配", 409)
            if source["capability"] != "send-private-messages":
                raise ServiceError("INVALID_REQUEST", "指定任务不是主动私信批次", 409)
            if source["state"] not in {
                "PARTIAL_SUCCESS",
                "FAILED",
                "BLOCKED",
                "RESULT_UNKNOWN",
            }:
                raise ServiceError("INVALID_TASK_STATE", "该私信批次当前不需要恢复", 409)
            if verified_uid != str(parameters.get("verified_uid") or ""):
                raise ServiceError("IDENTITY_MISMATCH", "当前登录账号与原私信任务账号不一致", 409)
            normalized = normalize_private_message_recipients(
                list(parameters.get("recipients") or [])
            )
            previous_results = list(parameters.get("recipient_results") or [])
            previous_by_user = {str(item.get("user_id") or ""): item for item in previous_results}
            retryable = {
                item["user_id"]
                for item in normalized
                if item["user_id"] not in previous_by_user
                or previous_by_user[item["user_id"]].get("state") in {"FAILED", "BLOCKED"}
            }
            if not retryable:
                raise ServiceError("NO_RETRYABLE_RECIPIENTS", "该批次没有可恢复的收件人", 409)
            source_task_id = source["task_id"]
        else:
            normalized = normalize_private_message_recipients(list(recipients or []))

        task = self.tasks.create(
            source="agent",
            account_slot=account_slot,
            capability="send-private-messages",
            request_summary=(
                f"恢复个性化私信：{len(normalized)} 人"
                if source_task_id
                else f"发送个性化私信：{len(normalized)} 人"
            ),
            target_type="user_batch",
            parameters={
                "workflow_type": "private_message_batch",
                "stage": "ready",
                "generated_by": "codex",
                "verified_uid": verified_uid,
                "recipients": normalized,
                "recipient_results": previous_results,
                "resume_from_task_id": source_task_id,
            },
        )
        return self._execute_private_message_batch(task["task_id"])

    def _execute_private_message_batch(self, task_id: str) -> dict:
        parent = self.tasks.get(task_id)
        if parent["state"] != "QUEUED":
            raise ServiceError("INVALID_TASK_STATE", "私信批次尚未进入可执行状态", 409)
        claimed = self.tasks.claim_for_execution(task_id)
        if claimed["state"] == "BLOCKED":
            self._record_task_event(claimed, task_id)
            return {"success": True, "task": claimed, "items": []}

        parameters = claimed.get("parameters") or {}
        recipients = normalize_private_message_recipients(parameters.get("recipients") or [])
        previous = {
            str(item.get("user_id") or ""): dict(item)
            for item in list(parameters.get("recipient_results") or [])
        }
        for recipient in recipients:
            old = previous.get(recipient["user_id"])
            if old and old.get("state") in {"SUCCESS", "RESULT_UNKNOWN"}:
                continue
            child = self.tasks.create(
                source="agent",
                account_slot=claimed["account_slot"],
                capability="send-private-messages",
                operation="recipient",
                request_summary=(f"发送私信：{recipient.get('nickname') or recipient['user_id']}"),
                parent_task_id=task_id,
                target_type="user",
                target_id=recipient["user_id"],
                parameters=recipient,
            )
            execution = self.execute_task(child["task_id"])
            child_task = execution["task"]
            previous[recipient["user_id"]] = {
                "user_id": recipient["user_id"],
                "nickname": recipient.get("nickname") or "",
                "contact_purpose": recipient["contact_purpose"],
                "conversation_type": (
                    ((execution.get("result") or {}).get("target") or {}).get("conversation_type")
                    or recipient["conversation_type"]
                ),
                "content": recipient["content"],
                "task_id": child_task["task_id"],
                "state": child_task["state"],
                "result_summary": child_task.get("result_summary") or "",
                "error_code": child_task.get("error_code") or "",
                "readback": (execution.get("result") or {}).get("readback") or {},
            }
            ordered = [
                previous[item["user_id"]] for item in recipients if item["user_id"] in previous
            ]
            self.tasks.update_parameters(task_id, recipient_results=ordered, stage="sending")

        items = [previous[item["user_id"]] for item in recipients if item["user_id"] in previous]
        states = [item["state"] for item in items]
        success_count = states.count("SUCCESS")
        if success_count == len(recipients):
            final_state = "SUCCESS"
        elif success_count:
            final_state = "PARTIAL_SUCCESS"
        elif "RESULT_UNKNOWN" in states:
            final_state = "RESULT_UNKNOWN"
        elif states and all(state == "BLOCKED" for state in states):
            final_state = "BLOCKED"
        else:
            final_state = "FAILED"
        summary = f"私信批次完成：{success_count}/{len(recipients)} 条确认发送成功"
        completed = self.tasks.transition(
            task_id,
            final_state,
            result_summary=summary,
            error_code=(
                ""
                if final_state in {"SUCCESS", "PARTIAL_SUCCESS"}
                else "PRIVATE_MESSAGE_BATCH_FAILED"
            ),
            recommended_action=(
                "先人工检查结果未知的会话，不要直接重发"
                if "RESULT_UNKNOWN" in states
                else "查看逐条结果"
            ),
        )
        result = {
            "result_type": "private_message_batch",
            "items": items,
            "count": len(recipients),
            "success_count": success_count,
            "conversation_counts": {
                "first_message": sum(
                    item.get("conversation_type") == "first_message" for item in items
                ),
                "existing": sum(item.get("conversation_type") == "existing" for item in items),
            },
            "partial": final_state == "PARTIAL_SUCCESS",
        }
        self._record_task_event(completed, task_id, result=result)
        return {"success": True, "task": completed, **result}

    def _private_message_verified_uid(self, account_slot: str) -> str:
        status = self.get_account_status(account_slot)
        identity = status.get("identity") or {}
        live_uid = _execution_uid(identity) or str(identity.get("user_id") or "")
        if not status.get("ready") or not live_uid:
            raise ServiceError(
                "ACCOUNT_NOT_READY",
                status.get("next_action") or "目标账号尚未完成身份核验",
                409,
            )
        return live_uid

    def _record_task_event(self, task: dict, target_key: str, *, result=None) -> dict:
        self.tasks.record_event(task, target_key, result=result)
        return self.operation_events.record(
            task,
            result=result,
            readback=(result or {}).get("readback") or {},
        )

    def _record_reply_execution(
        self,
        task: dict,
        *,
        draft: dict,
        approval: dict,
        source_event: dict | None,
        result: dict | None,
        operation_event: dict,
    ) -> dict | None:
        if not source_event or draft.get("action_type") not in {
            "reply-comment",
            "send-private-messages",
        }:
            return None
        return self.reply_records.record(
            task,
            draft=draft,
            approval=approval,
            source_event=source_event,
            result=result,
            operation_event_id=str(operation_event.get("operation_event_id") or ""),
        )

    @staticmethod
    def _task_target_key(task: dict) -> str:
        parameters = task.get("parameters") or {}
        raw = str(
            task.get("target_id")
            or parameters.get("feed_id")
            or parameters.get("user_id")
            or parameters.get("keyword")
            or ""
        )
        return raw

    @staticmethod
    def _validate_task_parameters(capability: str, parameters: dict) -> None:
        if capability in {
            "fill-publish",
            "fill-publish-video",
            "long-article",
            "click-publish",
            "save-draft",
            "follow-user-preview",
            "follow-user",
            "private-message-context",
            "send-private-messages",
        }:
            raise ServiceError(
                "AGENT_CLI_ONLY",
                "该任务只能由 Agent 通过 Python CLI 的预览流程创建",
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
                raise ServiceError(
                    "INVALID_REQUEST", "随机互动、候选池和搜集时间必须是整数"
                ) from exc
            if not 1 <= count <= 20:
                raise ServiceError("INVALID_REQUEST", "随机互动数量必须在 1 到 20 篇之间")
            if not count <= candidate_pool_size <= 100:
                raise ServiceError(
                    "INVALID_REQUEST", "候选池数量必须不少于互动数量，且不超过 100 篇"
                )
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
                raise ServiceError(
                    "INVALID_REQUEST", "评论数量、候选池和搜集时间必须是整数"
                ) from exc
            if not 1 <= count <= 3:
                raise ServiceError("INVALID_REQUEST", "随机评论数量必须在 1 到 3 篇之间")
            if not count <= candidate_pool_size <= 100:
                raise ServiceError(
                    "INVALID_REQUEST", "候选池数量必须不少于评论数量，且不超过 100 篇"
                )
            if not 1 <= collection_minutes <= 10:
                raise ServiceError("INVALID_REQUEST", "最长搜集时间必须在 1 到 10 分钟之间")
        if capability == "home-engagement":
            try:
                browse_count = int(parameters.get("browse_count"))
                like_count = int(parameters.get("like_count"))
                duration_minutes = int(parameters.get("duration_minutes"))
                min_read_seconds = float(parameters.get("min_read_seconds", 15))
                max_read_seconds = float(parameters.get("max_read_seconds", 45))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "首页互动参数必须是有效数字") from exc
            if not 1 <= browse_count <= 50:
                raise ServiceError("INVALID_REQUEST", "浏览数量必须在 1 到 50 篇之间")
            if not 0 <= like_count <= browse_count:
                raise ServiceError("INVALID_REQUEST", "点赞数量不能超过浏览数量")
            if not 1 <= duration_minutes <= 120:
                raise ServiceError("INVALID_REQUEST", "最长执行时间必须在 1 到 120 分钟之间")
            if min_read_seconds < 0 or max_read_seconds < min_read_seconds or max_read_seconds > 60:
                raise ServiceError(
                    "INVALID_REQUEST", "单篇阅读时间必须在 0 到 60 秒内且最短不大于最长"
                )
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
        if capability == "collect-note-comments":
            tracked_notes = parameters.get("tracked_notes")
            if tracked_notes is not None and not isinstance(tracked_notes, list):
                raise ServiceError("INVALID_REQUEST", "待跟踪笔记必须是列表")
            if tracked_notes and any(
                not isinstance(item, dict) or not str(item.get("feed_id") or "").strip()
                for item in tracked_notes
            ):
                raise ServiceError("INVALID_REQUEST", "每篇待跟踪笔记必须包含 Feed ID")
            try:
                max_notes = int(parameters.get("max_notes", 20))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "监测笔记数量必须是整数") from exc
            if not 1 <= max_notes <= 100:
                raise ServiceError("INVALID_REQUEST", "监测笔记数量必须在 1 到 100 篇之间")
        if capability == "collect-private-messages":
            try:
                max_conversations = int(parameters.get("max_conversations", 20))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "私信会话数量必须是整数") from exc
            if not 1 <= max_conversations <= 50:
                raise ServiceError("INVALID_REQUEST", "私信会话数量必须在 1 到 50 个之间")
        if capability == "collect-operations-metrics":
            try:
                max_notes = int(parameters.get("max_notes", 50))
            except (TypeError, ValueError) as exc:
                raise ServiceError("INVALID_REQUEST", "指标采集笔记数量必须是整数") from exc
            if not 1 <= max_notes <= 200:
                raise ServiceError("INVALID_REQUEST", "指标采集笔记数量必须在 1 到 200 篇之间")
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

    def _record_metric_collection(
        self,
        account_slot: str,
        result: dict,
        *,
        platform: str = "xiaohongshu",
    ) -> dict:
        captured_at = str(result.get("captured_at") or "")
        source = str(result.get("source") or "user_profile")
        account = result.get("account") or {}
        account_snapshot = self.metrics.record_snapshot(
            account_slot=account_slot,
            platform=platform,
            entity_type="account",
            entity_id=str(account.get("entity_id") or account_slot),
            captured_at=captured_at,
            source=source,
            metrics=account.get("metrics") or {},
            extra=account.get("extra") or {},
        )
        note_snapshots = [
            self.metrics.record_snapshot(
                account_slot=account_slot,
                platform=platform,
                entity_type="note",
                entity_id=str(note.get("entity_id") or ""),
                captured_at=captured_at,
                source=source,
                metrics=note.get("metrics") or {},
                extra={"title": note.get("title") or "", **(note.get("extra") or {})},
            )
            for note in result.get("notes") or []
            if note.get("entity_id")
        ]
        growth = result.get("growth_notifications") or {}
        growth_events = []
        for event in growth.get("events") or []:
            saved = self.inbound_events.record(
                account_slot=account_slot,
                platform=platform,
                event_type=event["event_type"],
                platform_event_id=event["platform_event_id"],
                occurred_at=event["occurred_at"],
                actor_user_id=event["actor_user_id"],
                object_type="notification",
                object_id=event["platform_event_id"],
                payload=dict(event),
                classification="growth_notification",
            )
            growth_events.append(saved["event"])
        return {
            "account": account_snapshot,
            "notes": note_snapshots,
            "events": growth_events,
            "note_count": len(note_snapshots),
            "growth_coverage": growth.get("coverage", "not_collected"),
        }

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
        xsec_token: str = "",
        feed_id: str = "",
        comment_id: str = "",
        user_id: str = "",
    ) -> dict:
        if self._store.get_setting("global_paused", False):
            raise ServiceError("GLOBAL_PAUSED", "自动化已全局暂停", 409)
        draft = self._store.get("drafts", draft_id)
        if draft is None:
            raise ServiceError("NOT_FOUND", "草稿不存在", 404)
        linked_task = self.tasks.get(draft["task_id"]) if draft.get("task_id") else None
        source_event: dict | None = None
        if linked_task and linked_task["state"] != "WAITING_APPROVAL":
            raise ServiceError(
                "INVALID_TASK_STATE",
                "被动回复任务已经离开待确认状态",
                409,
            )
        private_message_reply = draft["action_type"] == "send-private-messages" and bool(
            draft.get("source_event_id")
        )
        if private_message_reply:
            source_event = self.inbound_events.get(draft["source_event_id"])
            if source_event.get("event_type") != "private_message":
                raise ServiceError("INVALID_EVENT_TYPE", "该草稿不是私信回复事件", 409)
            event_context = source_event.get("payload") or {}
            task_context = (linked_task or {}).get("parameters") or {}
            user_id = str(
                task_context.get("user_id")
                or event_context.get("user_id")
                or source_event.get("actor_user_id")
                or ""
            )
            nickname = str(task_context.get("nickname") or event_context.get("nickname") or "")
            original_content = str(
                task_context.get("original_content") or event_context.get("content") or ""
            )
            notification_id = ""
            feed_id = ""
            comment_id = ""
            xsec_token = ""
        elif draft["action_type"] == "reply-comment" and draft.get("source_event_id"):
            source_event = self.inbound_events.get(draft["source_event_id"])
            event_context = source_event.get("payload") or {}
            task_context = (linked_task or {}).get("parameters") or {}
            notification_id = str(
                task_context.get("notification_id") or event_context.get("notification_id") or ""
            )
            feed_id = str(task_context.get("feed_id") or event_context.get("feed_id") or "")
            xsec_token = str(
                task_context.get("xsec_token") or event_context.get("xsec_token") or ""
            )
            comment_id = str(
                task_context.get("comment_id")
                or event_context.get("comment_id")
                or draft["target_id"]
            )
            user_id = str(
                task_context.get("user_id")
                or event_context.get("user_id")
                or source_event.get("actor_user_id")
                or ""
            )
            nickname = str(task_context.get("nickname") or event_context.get("nickname") or "")
            original_content = str(
                task_context.get("original_content") or event_context.get("content") or ""
            )
        else:
            notification_id = ""
            nickname = ""
            original_content = ""
        execution_target = (
            user_id
            if private_message_reply
            else feed_id
            if draft["action_type"] == "post-comment"
            else notification_id or comment_id
        )
        if execution_target != draft["target_id"]:
            raise ServiceError("CONFIRMATION_MISMATCH", "执行目标与已确认草稿不一致", 409)
        if (
            not private_message_reply
            and not notification_id
            and (not xsec_token.strip() or not feed_id.strip())
        ):
            raise ServiceError("INVALID_REQUEST", "执行评论或回复需要笔记信息")
        status = self.get_account_status(draft["account_slot"])
        if not status["ready"]:
            raise ServiceError("ACCOUNT_NOT_READY", status["next_action"] or "账号未就绪", 409)
        live_uid = _execution_uid(status["identity"])
        consumed = self.approvals.consume(
            approval_id,
            account_slot=draft["account_slot"],
            verified_uid=live_uid,
            action_type=draft["action_type"],
            target_id=draft["target_id"],
        )
        task_parameters = {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "comment_id": comment_id,
            "notification_id": notification_id,
            "user_id": user_id,
            "nickname": nickname,
            "original_content": original_content,
            "content": draft["content"],
        }
        if linked_task:
            task = linked_task
            self.tasks.update_parameters(task["task_id"], **task_parameters)
            task = self.tasks.transition(task["task_id"], "QUEUED")
        else:
            task = self.tasks.create(
                source="webui",
                account_slot=draft["account_slot"],
                capability=draft["action_type"],
                request_summary=f"确认后执行 {draft['action_type']}",
                parameters=task_parameters,
            )
            task = self.tasks.transition(task["task_id"], "QUEUED")
        claimed = self.tasks.claim_for_execution(task["task_id"])
        if claimed["state"] == "BLOCKED":
            operation_event = self._record_task_event(claimed, draft["target_id"])
            self._record_reply_execution(
                claimed,
                draft=draft,
                approval=consumed["approval"],
                source_event=source_event,
                result=None,
                operation_event=operation_event,
            )
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
            result_unknown = bool(getattr(exc, "result_unknown", True))
            final_state = "RESULT_UNKNOWN" if result_unknown else "FAILED"
            completed = self.tasks.transition(
                task["task_id"],
                final_state,
                result_summary=message,
                error_code=code,
                recommended_action=(
                    "请先在小红书页面人工检查结果，不要直接重发"
                    if result_unknown
                    else "本次未点击发送；请刷新通知后重新生成草稿"
                ),
            )
            self.approvals.mark_execution(draft_id, final_state)
            failure_result = {"message": message}
            operation_event = self._record_task_event(
                completed,
                draft["target_id"],
                result=failure_result,
            )
            self._record_reply_execution(
                completed,
                draft=draft,
                approval=consumed["approval"],
                source_event=source_event,
                result=failure_result,
                operation_event=operation_event,
            )
            return {"success": True, "task": completed, "approval": consumed["approval"]}
        completed = self.tasks.transition(
            task["task_id"], "SUCCESS", result_summary=self._result_summary(result)
        )
        self.approvals.mark_execution(draft_id, "EXECUTED")
        operation_event = self._record_task_event(
            completed,
            draft["target_id"],
            result=result,
        )
        self._record_reply_execution(
            completed,
            draft=draft,
            approval=consumed["approval"],
            source_event=source_event,
            result=result,
            operation_event=operation_event,
        )
        if draft.get("source_event_id"):
            self.inbound_events.mark_handled(draft["source_event_id"])
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

    def _resolve_account_platform(self, account: str) -> str:
        try:
            config = self._account_loader(account)
        except (FileNotFoundError, ValueError):
            return "xiaohongshu"
        return normalize_platform(getattr(config, "platform", "xiaohongshu"))

    def _page_for_account(
        self,
        config: AccountConfig,
        *,
        bridge_url: str | None = None,
    ) -> BridgePage:
        platform = normalize_platform(getattr(config, "platform", "xiaohongshu"))
        page_args = {
            "account": config.name,
            "account_id": config.account_id,
            "bridge_token": config.bridge_token,
            "platform": platform,
        }
        try:
            page = self._page_factory(bridge_url or config.bridge_url, **page_args)
        except TypeError as exc:
            if "platform" not in str(exc):
                raise
            page_args.pop("platform")
            page = self._page_factory(bridge_url or config.bridge_url, **page_args)
        if not hasattr(page, "platform"):
            setattr(page, "platform", platform)
        return page

    def _validate_publish_confirmation_identity(
        self,
        account_slot: str,
        verified_uid: str,
    ) -> dict:
        status = self.get_account_status(account_slot)
        if not status["ready"]:
            raise ServiceError(
                "ACCOUNT_NOT_READY",
                status.get("next_action") or "发布账号尚未就绪",
                409,
            )
        identity = status.get("identity") or {}
        recorded_uid = str(identity.get("user_id") or "").strip()
        live_uid = _execution_uid(identity).strip()
        expected_uid = str(verified_uid or "").strip()
        if not expected_uid or expected_uid != recorded_uid or expected_uid != live_uid:
            raise ServiceError(
                "CONFIRMATION_MISMATCH",
                "确认页面中的 UID 与当前槽位身份不一致，请刷新后重新核对",
                409,
            )
        return status

    def _ensure_slot_capacity(self) -> None:
        if len(self._account_lister()) >= 6:
            raise ServiceError("ACCOUNT_LIMIT_REACHED", "V1 最多支持 6 个账号槽位", 409)

    def _identity_summary(self, account: str) -> dict:
        platform = self._resolve_account_platform(account)
        record = self._identity_loader(account)
        switch = self._switch_loader(account)
        current = (record or {}).get("current") or {}
        runtime_checks = self._store.get_setting("runtime_identity_checks", {})
        live = runtime_checks.get(account) or {}
        recorded_uid = str(current.get("user_id") or "")
        live_uid = str(live.get("user_id") or "")
        recorded_platform = normalize_platform(current.get("platform") or platform)
        live_platform = normalize_platform(live.get("platform") or platform)
        logged_out = bool(live) and live.get("logged_in") is False
        execution_uid = "" if logged_out else (live_uid or recorded_uid)
        return {
            "platform": platform,
            "recorded": bool(record),
            "user_id": recorded_uid,
            "nickname": str(current.get("nickname") or ""),
            "observed_at": str(current.get("observed_at") or ""),
            "live_checked": bool(live_uid),
            "live_user_id": live_uid,
            "execution_user_id": execution_uid,
            "execution_identity_source": "runtime_check" if live else "slot_binding",
            "live_nickname": str(live.get("nickname") or ""),
            "logged_out": logged_out,
            "logout_confirmed": live.get("event") == "LOGOUT_CONFIRMED",
            "matches_record": bool(
                recorded_uid
                and execution_uid == recorded_uid
                and recorded_platform == platform
                and live_platform == platform
            ),
            "switch_pending": bool(switch),
            "switch": switch,
        }

    def require_account_identity(self, account: str) -> dict:
        """读取已绑定身份及已知登录状态，供业务连接使用。"""
        identity = self._identity_summary(account)
        state, next_action = _identity_state(identity)
        if state != "READY":
            raise ServiceError(state, next_action or "请完成账号身份绑定", 409)
        return identity

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
        platform = normalize_platform(getattr(page, "platform", None))
        identity = {
            **identity,
            "platform": normalize_platform(identity.get("platform") or platform),
        }
        if require_uid and not identity.get("logged_in"):
            raise ServiceError("LOGIN_REQUIRED", "当前 Profile 尚未登录目标平台", 409)
        if require_uid and not identity.get("user_id"):
            raise ServiceError(
                "IDENTITY_UID_UNAVAILABLE",
                "已检测到登录，但无法读取当前 UID，请刷新目标平台首页后重试",
                409,
            )
        return identity

    def _remember_runtime_identity(self, account: str, identity: dict) -> None:
        checks = dict(self._store.get_setting("runtime_identity_checks", {}))
        checks[account] = {
            "platform": normalize_platform(
                identity.get("platform") or self._resolve_account_platform(account)
            ),
            "logged_in": True,
            "user_id": str(identity.get("user_id") or ""),
            "nickname": str(identity.get("nickname") or ""),
            "observed_at": str(identity.get("observed_at") or ""),
        }
        self._store.set_setting("runtime_identity_checks", checks)

    def _remember_runtime_logged_out(
        self,
        account: str,
        identity: dict | None = None,
        *,
        event: str = "LOGIN_REQUIRED",
    ) -> None:
        observed = identity or {}
        checks = dict(self._store.get_setting("runtime_identity_checks", {}))
        checks[account] = {
            "platform": normalize_platform(
                observed.get("platform") or self._resolve_account_platform(account)
            ),
            "logged_in": False,
            "user_id": "",
            "nickname": "",
            "observed_at": str(observed.get("observed_at") or ""),
            "event": event,
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
    if not runtime.get("platform_matches", True):
        return "PLATFORM_MISMATCH", "当前扩展页面平台与账号槽位平台不一致"
    if not runtime["profile_verified"]:
        return "BLOCKED", "检查 Profile 绑定和扩展配对"
    return _identity_state(identity)


def _identity_state(identity: dict) -> tuple[str, str | None]:
    if identity["switch_pending"]:
        return "SWITCH_PENDING", "在对应 Chrome Profile 登录新账号，然后完成换号核验"
    if identity["logged_out"]:
        return "LOGIN_REQUIRED", "请在绑定的 Chrome Profile 中手动登录账号，登录后完成身份核验"
    if not identity["recorded"] or not identity["user_id"]:
        return "IDENTITY_REQUIRED", "在 WebUI 中完成登录身份核验"
    if not identity["matches_record"]:
        return "IDENTITY_MISMATCH", "当前登录账号与槽位记录不一致，请停止任务并检查账号"
    return "READY", None


def _execution_uid(identity: dict) -> str:
    """优先使用执行身份，兼容已有状态提供方的 UID 字段。"""
    return str(identity.get("execution_user_id") or identity.get("live_user_id") or "")
