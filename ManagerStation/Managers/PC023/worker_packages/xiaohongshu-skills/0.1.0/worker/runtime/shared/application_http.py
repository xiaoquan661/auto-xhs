"""HTTP routes shared by the local application and employee executor."""

from __future__ import annotations

from http.server import ThreadingHTTPServer
from typing import TYPE_CHECKING
from urllib.parse import unquote

from service_errors import ServiceError

if TYPE_CHECKING:
    from application_service import ApplicationService

API_PREFIX = "/api/v1"


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Keep one local control plane per listening port."""

    allow_reuse_address = False
    allow_reuse_port = False


def _dispatch_api(
    service: ApplicationService,
    path: str,
) -> dict:
    if path == f"{API_PREFIX}/health":
        return service.health()
    if path == f"{API_PREFIX}/capabilities":
        return service.list_capabilities()
    if path == f"{API_PREFIX}/accounts":
        return service.list_accounts()
    if path == f"{API_PREFIX}/accounts/profiles":
        return service.discover_profiles()
    if path == f"{API_PREFIX}/doctor":
        return service.doctor_account()
    if path == f"{API_PREFIX}/system/status":
        return service.system_status()
    if path == f"{API_PREFIX}/tasks":
        return service.list_tasks()
    if path == f"{API_PREFIX}/drafts":
        return service.list_drafts()
    if path == f"{API_PREFIX}/records":
        return service.list_records()
    if path == f"{API_PREFIX}/inbound-events":
        return service.list_inbound_events()
    if path == f"{API_PREFIX}/operation-events":
        return service.list_operation_events()
    if path == f"{API_PREFIX}/data-recovery/summary":
        return service.data_recovery_summary()
    if path == f"{API_PREFIX}/data-recovery/published-posts":
        return service.list_published_posts()
    if path == f"{API_PREFIX}/data-recovery/operation-records":
        return service.list_operation_records()
    if path == f"{API_PREFIX}/data-recovery/reply-records":
        return service.list_reply_records()
    if path == f"{API_PREFIX}/reply-rules":
        return service.list_reply_rules()
    if path == f"{API_PREFIX}/reply-cycles":
        return service.list_reply_cycles()
    if path == f"{API_PREFIX}/reply-decisions":
        return service.list_reply_decisions()
    if path == f"{API_PREFIX}/operation-reports":
        return service.list_operation_reports()
    if path == f"{API_PREFIX}/reply-intelligence/status":
        return service.intelligent_reply_status()
    if path == f"{API_PREFIX}/action-drafts":
        return service.list_action_drafts()

    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) == 5 and parts[:3] == ["api", "v1", "accounts"]:
        account, action = parts[3], parts[4]
        if action == "status":
            return service.get_account_status(account)
        if action == "doctor":
            return service.doctor_account(account)
        if action == "pairing":
            return service.account_pairing_status(account)
        if action == "bridge":
            return service.get_bridge_status(account)
        if action == "switch":
            return service.get_account_switch(account)
    if len(parts) == 4 and parts[:3] == ["api", "v1", "tasks"]:
        return service.get_task(parts[3])
    if len(parts) == 4 and parts[:3] == ["api", "v1", "inbound-events"]:
        return service.get_inbound_event(parts[3])
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "data-recovery"]
        and parts[3] == "published-posts"
    ):
        return service.get_published_post(parts[4])
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "data-recovery"]
        and parts[3] == "reply-records"
    ):
        return service.get_reply_record(parts[4])
    if len(parts) == 6 and parts[:3] == ["api", "v1", "metrics"]:
        return service.get_metric_history(parts[3], parts[4], parts[5])
    if len(parts) == 4 and parts[:3] == ["api", "v1", "operation-reports"]:
        return service.get_operation_report(parts[3])
    if len(parts) == 7 and parts[:3] == ["api", "v1", "metrics"] and parts[6] == "delta":
        return service.get_metric_delta(parts[3], parts[4], parts[5])
    raise ServiceError("NOT_FOUND", "接口不存在", 404)


def _dispatch_mutation(
    service: ApplicationService,
    method: str,
    path: str,
    body: dict,
) -> dict:
    if method == "POST" and path == f"{API_PREFIX}/system/pause":
        return service.set_global_pause(True)
    if method == "POST" and path == f"{API_PREFIX}/system/resume":
        return service.set_global_pause(False)
    if method == "POST" and path == f"{API_PREFIX}/system/restart":
        return service.restart_local_service()
    if method == "POST" and path == f"{API_PREFIX}/diagnostics/export":
        return service.export_diagnostics()
    if method == "POST" and path == f"{API_PREFIX}/system/settings":
        return service.update_system_settings(**body)
    if method == "POST" and path == f"{API_PREFIX}/reply-intelligence/settings":
        return service.update_reply_model_settings(**body)
    if method == "POST" and path == f"{API_PREFIX}/reply-intelligence/test":
        return service.test_reply_model_connection()
    if method == "POST" and path == f"{API_PREFIX}/tasks":
        return service.create_task(**body)
    if method == "POST" and path == f"{API_PREFIX}/drafts":
        return service.create_draft(**body)
    if method == "POST" and path == f"{API_PREFIX}/reply-rules":
        return service.create_reply_rule(**body)
    if method == "POST" and path == f"{API_PREFIX}/reply-cycle":
        return service.run_reply_cycle(**body)
    if method == "POST" and path == f"{API_PREFIX}/operation-reports":
        return service.generate_operation_report(**body)
    if method == "POST" and path == f"{API_PREFIX}/action-drafts":
        return service.create_action_draft(**body)
    if method == "POST" and path == f"{API_PREFIX}/accounts":
        return service.create_account_slot(**body)
    if method == "POST" and path == f"{API_PREFIX}/accounts/import":
        return service.import_account_slot(**body)
    if method == "POST" and path == f"{API_PREFIX}/accounts/discover":
        return service.discover_profiles(body.get("user_data_dir"))

    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "operation-reports"]
        and parts[4] == "revisions"
        and method == "POST"
    ):
        return service.revise_operation_report(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "inbound-events"]
        and method == "POST"
        and parts[4] == "reply-draft"
    ):
        return service.create_passive_reply_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "inbound-events"]
        and method == "POST"
        and parts[4] == "intelligent-reply-draft"
    ):
        return service.create_intelligent_reply_draft(parts[3], **body)
    if len(parts) == 4 and parts[:3] == ["api", "v1", "reply-rules"] and method == "PATCH":
        return service.update_reply_rule(parts[3], **body)
    if len(parts) == 4 and parts[:3] == ["api", "v1", "action-drafts"] and method == "PATCH":
        return service.update_action_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "action-drafts"]
        and parts[4] == "confirm"
        and method == "POST"
    ):
        return service.confirm_action_draft(
            parts[3],
            ttl_seconds=int(body.get("ttl_seconds", 300)),
        )
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "reply-rules"]
        and parts[4] == "enabled"
        and method == "POST"
    ):
        return service.set_reply_rule_enabled(parts[3], **body)
    if len(parts) == 4 and parts[:3] == ["api", "v1", "accounts"] and method == "DELETE":
        return service.remove_account_slot(parts[3], **body)
    if len(parts) == 4 and parts[:3] == ["api", "v1", "drafts"] and method == "PATCH":
        return service.update_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "drafts"]
        and method == "POST"
        and parts[4] == "confirm"
    ):
        return service.confirm_draft(
            parts[3],
            ttl_seconds=int(body.get("ttl_seconds", 300)),
        )
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "drafts"]
        and method == "POST"
        and parts[4] == "execute"
    ):
        return service.execute_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "publish-confirm"
    ):
        return service.confirm_publish_task(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "save-draft"
    ):
        return service.save_publish_task_as_draft(parts[3], **body)
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "execute"
    ):
        return service.execute_task(parts[3])
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "retry"
    ):
        return service.retry_task(parts[3])
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "tasks"]
        and method == "POST"
        and parts[4] == "cancel"
    ):
        return service.cancel_task(parts[3])
    if len(parts) == 6 and parts[:3] == ["api", "v1", "accounts"]:
        account, section, action = parts[3], parts[4], parts[5]
        if method == "POST" and (section, action) == ("pairing", "begin"):
            return service.begin_account_pairing(account, **body)
        if method == "POST" and (section, action) == ("setup", "begin"):
            return service.begin_account_setup(account, **body)
        if method == "POST" and (section, action) == ("identity", "check"):
            return service.check_account_identity(account)
        if method == "POST" and (section, action) == ("identity", "record"):
            return service.record_account_identity(account, **body)
        if method == "POST" and (section, action) == ("identity", "replace"):
            return service.replace_account_identity(account, **body)
        if method == "POST" and (section, action) == ("auth", "logout"):
            return service.logout_account(account, **body)
        if method == "POST" and (section, action) == ("switch", "begin"):
            return service.begin_account_switch(account, **body)
        if method == "POST" and (section, action) == ("switch", "complete"):
            return service.complete_account_switch(account, **body)
        if method == "POST" and (section, action) == ("switch", "cancel"):
            return service.cancel_account_switch(account, **body)
        if method == "POST" and (section, action) == ("bridge", "start"):
            return service.start_account_bridge(account)
        if method == "POST" and (section, action) == ("bridge", "start-only"):
            return service.start_account_bridge_only(account)
        if method == "POST" and (section, action) == ("bridge", "stop"):
            return service.stop_account_bridge(account)
        if method == "POST" and (section, action) == ("bridge", "restart"):
            return service.restart_account_bridge(account)
    raise ServiceError("NOT_FOUND", "接口不存在", 404)
