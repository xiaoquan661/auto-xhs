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
)
from account_pairing import (
    create_pairing_session,
    get_pairing_status,
    revoke_account_pairing,
)
from account_runtime import evaluate_profile_connection
from action_workflow_service import ActionWorkflowService
from approval_service import ApprovalService
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
        self.private_message_conversations = ConversationOwnershipService(
            self.operations_database
        )
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
    ) -> dict:
        if not confirmed:
            raise ServiceError("CONFIRMATION_REQUIRED", "创建配对请求需要明确确认", 409)
        self.require_enabled_capability("account-pair-begin")
        config = self._load_account(account)
        try:
            pairing = self._pairing_creator(config, ttl_seconds=ttl_seconds)
        except (RuntimeError, ValueError) as exc:
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
            elif exc.code == "IDENTITY_UID_UNAVAILABLE":
                self._clear_runtime_identity_check(account)
            raise
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
        self._validate_task_parameters(
            str(values.get("capability") or ""),
            values.get("parameters") or {},
        )
        return {"success": True, "task": self.tasks.create(**values)}

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
        task = self.publish_workflows.prepare_publish(
            task_id,
            account=account_slot,
            confirmed=True,
            preview_revision=preview_revision,
        )
        preview = (task.get("parameters") or {}).get("preview") or {}
