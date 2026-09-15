"""HTTP server for the auto-xhs WebUI on the manager and local network."""

from __future__ import annotations

import argparse
import gzip
import json
import mimetypes
import sys
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

try:
    from .application_service import ApplicationService, ServiceError
    from .manager_agent_service import ManagerAgentService
    from .manager_service import ManagerService
    from .manager_sync_service import ManagerSyncService
    from .web_lifecycle import schedule_webui_restart
    from .web_session import MAX_JSON_BODY, SESSION_HEADER, create_session_token
except ImportError:  # Direct execution: python scripts/web_server.py
    from application_service import ApplicationService, ServiceError
    from manager_agent_service import ManagerAgentService
    from manager_service import ManagerService
    from manager_sync_service import ManagerSyncService
    from web_lifecycle import schedule_webui_restart
    from web_session import MAX_JSON_BODY, SESSION_HEADER, create_session_token


WEB_ROOT = Path(__file__).resolve().parent.parent / "webui"


class LocalThreadingHTTPServer(ThreadingHTTPServer):
    """Keep the local control plane exclusive to one process per port."""

    allow_reuse_address = False
    allow_reuse_port = False


API_PREFIX = "/api/v1"


def _validate_bind_host(host: str) -> str:
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("WebUI 监听地址请选择 127.0.0.1（本机）或 0.0.0.0（局域网）")
    return host


def make_handler(
    service: ApplicationService,
    *,
    web_root: Path = WEB_ROOT,
    session_token: str | None = None,
    manager_service: ManagerService | None = None,
) -> type[BaseHTTPRequestHandler]:
    token = session_token or create_session_token()

    class LocalWebHandler(BaseHTTPRequestHandler):
        server_version = "auto-xhs-local/1"

        def do_GET(self) -> None:
            path = urlparse(self.path).path
            if path.startswith(API_PREFIX):
                self._handle_api(path)
                return
            self._serve_static(path)

        def do_POST(self) -> None:
            self._handle_mutation("POST")

        def do_PATCH(self) -> None:
            self._handle_mutation("PATCH")

        def do_DELETE(self) -> None:
            self._handle_mutation("DELETE")

        def log_message(self, _format: str, *_args) -> None:
            return

        def _handle_api(self, path: str) -> None:
            try:
                payload = (
                    {"success": True, "session_token": token}
                    if path == f"{API_PREFIX}/session"
                    else _dispatch_api(service, path, manager_service)
                )
            except ServiceError as exc:
                self._send_json(exc.http_status, exc.to_dict())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "success": False,
                        "error": {
                            "code": "INTERNAL_ERROR",
                            "message": "本地服务处理请求失败，请查看诊断",
                        },
                    },
                )
                return
            self._send_json(HTTPStatus.OK, payload)

        def _handle_mutation(self, method: str) -> None:
            path = urlparse(self.path).path
            if not path.startswith(API_PREFIX):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if self.headers.get(SESSION_HEADER) != token:
                self._send_json(
                    HTTPStatus.FORBIDDEN,
                    ServiceError(
                        "SESSION_REQUIRED",
                        "本地会话已失效，请刷新 WebUI",
                        403,
                    ).to_dict(),
                )
                return
            try:
                payload = _dispatch_mutation(
                    service,
                    method,
                    path,
                    self._read_json(),
                    manager_service,
                )
            except ServiceError as exc:
                self._send_json(exc.http_status, exc.to_dict())
                return
            except Exception:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    ServiceError(
                        "INTERNAL_ERROR",
                        "本地服务处理请求失败，请查看诊断",
                        500,
                    ).to_dict(),
                )
                return
            self._send_json(HTTPStatus.OK, payload)

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ServiceError("INVALID_REQUEST", "请求长度无效") from exc
            if length > MAX_JSON_BODY:
                raise ServiceError("INVALID_REQUEST", "请求内容过大", 413)
            if length == 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ServiceError("INVALID_REQUEST", "请求必须是 JSON") from exc
            if not isinstance(payload, dict):
                raise ServiceError("INVALID_REQUEST", "JSON 请求必须是对象")
            return payload

        def _serve_static(self, path: str) -> None:
            relative = "index.html" if path in {"", "/"} else unquote(path.lstrip("/"))
            target = (web_root / relative).resolve()
            try:
                target.relative_to(web_root.resolve())
            except ValueError:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            if not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            content = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            if target.name == "index.html":
                self.send_header(SESSION_HEADER, token)
            self.end_headers()
            self.wfile.write(content)

        def _send_json(self, status: int, payload: dict) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            encodings = self.headers.get("Accept-Encoding", "").lower().split(",")
            compressed = len(content) >= 4096 and any(
                item.strip() == "gzip" for item in encodings
            )
            if compressed:
                content = gzip.compress(content, compresslevel=1)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Vary", "Accept-Encoding")
            if compressed:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

    return LocalWebHandler


def _dispatch_api(
    service: ApplicationService,
    path: str,
    manager_service: ManagerService | None = None,
) -> dict:
    manager = _dispatch_manager_api(manager_service, path)
    if manager is not None:
        return manager
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
    manager_service: ManagerService | None = None,
) -> dict:
    manager = _dispatch_manager_mutation(manager_service, method, path, body)
    if manager is not None:
        return manager
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


def _dispatch_manager_api(
    manager_service: ManagerService | None,
    path: str,
) -> dict | None:
    prefix = f"{API_PREFIX}/manager"
    if not path.startswith(f"{prefix}/"):
        return None
    manager = _require_manager_service(manager_service)
    if path == f"{prefix}/debug":
        return manager.debug_action("list")
    if path == f"{prefix}/dashboard-snapshot":
        return manager.dashboard_snapshot()
    if path == f"{prefix}/database/schema":
        return manager.query_business_data()
    if path == f"{prefix}/acceptance":
        return manager.operations_acceptance()
    if path == f"{prefix}/polling-settings":
        return manager.get_polling_settings()
    if path == f"{prefix}/employee-summary":
        return manager.employee_summary()
    if path == f"{prefix}/machines":
        return manager.list_machines()
    if path == f"{prefix}/accounts":
        return manager.list_accounts()
    if path == f"{prefix}/personas":
        return manager.list_personas()
    if path == f"{prefix}/daily-plans":
        return manager.list_daily_plans()
    if path == f"{prefix}/goals":
        return manager.list_goals()
    if path == f"{prefix}/operation-tasks":
        return manager.list_operation_tasks()
    if path == f"{prefix}/agent-messages":
        return manager.list_agent_messages()
    if path == f"{prefix}/alerts":
        return manager.list_account_alerts()
    if path == f"{prefix}/sync-status":
        return manager.list_sync_status()
    if path == f"{prefix}/tasks":
        return manager.list_tasks()
    if path == f"{prefix}/schedules":
        return manager.list_schedules()
    if path == f"{prefix}/operation-reports":
        return manager.list_operation_reports()
    if path == f"{prefix}/transfers":
        return manager.list_transfers()
    if path == f"{prefix}/results":
        return manager.list_results()

    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 6
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
    ):
        return manager.list_machine_accounts(parts[4])
    if (
        len(parts) == 8
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
        and parts[7] == "status"
    ):
        return manager.get_machine_account_status(parts[4], parts[6])
    if len(parts) == 5 and parts[:4] == ["api", "v1", "manager", "tasks"]:
        return manager.get_task(parts[4])
    if len(parts) == 5 and parts[:4] == ["api", "v1", "manager", "personas"]:
        return manager.get_persona(parts[4])
    if len(parts) == 5 and parts[:4] == ["api", "v1", "manager", "goals"]:
        return manager.get_goal(parts[4])
    if len(parts) == 5 and parts[:4] == ["api", "v1", "manager", "operation-tasks"]:
        return manager.get_operation_task(parts[4])
    if (
        len(parts) == 6
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "versions"
    ):
        return manager.list_function_versions(parts[4])
    if (
        len(parts) == 7
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "versions"
    ):
        return manager.list_function_versions(parts[4], parts[6])
    raise ServiceError("NOT_FOUND", "经理机接口不存在", 404)


def _dispatch_manager_mutation(
    manager_service: ManagerService | None,
    method: str,
    path: str,
    body: dict,
) -> dict | None:
    prefix = f"{API_PREFIX}/manager"
    if not path.startswith(f"{prefix}/"):
        return None
    manager = _require_manager_service(manager_service)
    if method == "POST" and path.startswith(f"{prefix}/debug/"):
        return manager.debug_action(path.rsplit("/", 1)[-1], body)
    if method == "POST" and path == f"{prefix}/database/query":
        return manager.query_business_data(body)
    if method == "POST" and path == f"{prefix}/acceptance":
        return manager.operations_acceptance(body)
    if method == "POST" and path == f"{prefix}/polling-settings":
        return manager.update_polling_settings(body)
    if method == "POST" and path.startswith(f"{prefix}/memory/"):
        return manager.memory_action(path.rsplit("/", 1)[-1], body)
    if method == "POST" and path == f"{prefix}/machines":
        return manager.register_machine(body)
    if method == "POST" and path == f"{prefix}/tasks":
        return manager.dispatch_task(body)
    if method in {"POST", "PATCH"} and path == f"{prefix}/personas":
        return manager.save_persona(body)
    if method == "POST" and path.startswith(f"{prefix}/daily-plans/") and path.endswith("/prepare"):
        return manager.prepare_daily_plan(path.split("/")[-2], body)
    if method == "POST" and path == f"{prefix}/daily-plans":
        return manager.save_daily_plan(body)
    if method == "POST" and path == f"{prefix}/daily-plans/generate":
        return manager.generate_daily_plan(body)
    if method == "POST" and path == f"{prefix}/daily-plans/preview":
        return manager.preview_daily_plan(body)
    if method == "POST" and path == f"{prefix}/daily-plans/submit":
        return manager.submit_daily_plan(body)
    if (method == "POST" and path.startswith(f"{prefix}/operation-tasks/")
            and path.endswith("/daily-inputs")):
        return manager.supply_daily_inputs(path.split("/")[-2], body)
    if method in {"POST", "PATCH"} and path == f"{prefix}/goals":
        return manager.save_goal(body)
    if method == "POST" and path == f"{prefix}/operation-tasks":
        return manager.create_operation_task(body)
    if method == "POST" and path == f"{prefix}/schedules":
        return manager.register_schedule(body)
    if method == "POST" and path == f"{prefix}/operation-reports":
        return manager.generate_operation_report(body)
    if method == "POST" and path == f"{prefix}/transfers":
        return manager.transfer(body)
    if method == "POST" and path == f"{prefix}/version-policies":
        return manager.set_function_version_policy(body)

    parts = [unquote(part) for part in path.split("/") if part]
    if (
        method == "POST"
        and len(parts) == 6
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "account-lifecycle"
    ):
        return manager.manage_remote_account(parts[4], body)
    if (
        len(parts) == 6
        and parts[:4] == ["api", "v1", "manager", "results"]
        and parts[5] == "attribution"
        and method in {"POST", "PATCH"}
    ):
        return manager.save_action_attribution(parts[4], body)
    if (
        len(parts) == 8
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
        and parts[7] == "operations"
        and method in {"POST", "PATCH"}
    ):
        return manager.save_account_operations(parts[4], parts[6], body)
    if (
        len(parts) == 8
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
        and method == "POST"
        and parts[7] == "doctor"
    ):
        return manager.doctor_machine_account(parts[4], parts[6])
    if (
        len(parts) == 8
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
        and method in {"POST", "PATCH"}
        and parts[7] == "management"
    ):
        return manager.update_account_management(parts[4], parts[6], body)
    if (
        len(parts) == 8
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
        and method == "POST"
        and parts[7] == "field-request"
    ):
        return manager.request_machine_account_field_action(parts[4], parts[6], body)
    if (
        len(parts) == 9
        and parts[:4] == ["api", "v1", "manager", "machines"]
        and parts[5] == "accounts"
        and method == "POST"
    ):
        actions = {
            ("bridge", "restart"): "bridge-restart",
            ("bridge", "start"): "bridge-start",
            ("profile", "start"): "profile-start",
            ("identity", "check"): "identity-check",
        }
        account_action = actions.get((parts[7], parts[8]))
        if account_action:
            return manager.run_machine_account_action(parts[4], parts[6], account_action)
    if len(parts) == 6 and parts[:4] == ["api", "v1", "manager", "machines"]:
        machine_id, action = parts[4], parts[5]
        if method == "POST" and action == "check":
            return manager.check_machine(machine_id)
        if method == "POST" and action == "executor-status":
            return manager.machine_executor_status(machine_id)
        if method == "POST" and action == "executor-start":
            return manager.start_machine_executor(machine_id)
        if method == "POST" and action == "executor-restart":
            return manager.restart_machine_executor(machine_id)
        if method == "POST" and action == "webui-status":
            return manager.machine_webui_status(machine_id)
        if method == "POST" and action == "webui-start":
            return manager.start_machine_webui(machine_id)
        if method == "POST" and action == "webui-restart":
            return manager.restart_machine_webui(machine_id)
        if method == "POST" and action == "pause":
            return manager.pause_machine(machine_id)
        if method == "POST" and action == "resume":
            return manager.resume_machine(machine_id)
        if method == "POST" and action == "collect":
            return manager.collect_results(
                machine_id,
                download_evidence=bool(body.get("download_evidence", True)),
            )
    if len(parts) == 6 and parts[:4] == ["api", "v1", "manager", "tasks"]:
        task_id, action = parts[4], parts[5]
        if method == "POST" and action == "reconcile":
            return manager.reconcile_task(task_id)
        if method == "POST" and action == "pause":
            return manager.pause_task(task_id)
        if method == "POST" and action == "resume":
            return manager.resume_task(task_id)
    if len(parts) == 6 and parts[:4] == ["api", "v1", "manager", "schedules"]:
        schedule_id, action = parts[4], parts[5]
        if method == "POST" and action == "pause":
            return manager.pause_schedule(schedule_id)
        if method == "POST" and action == "resume":
            return manager.resume_schedule(schedule_id)
        if method == "POST" and action == "runs":
            return manager.record_schedule_run(schedule_id, body)
    if len(parts) == 6 and parts[:4] == ["api", "v1", "manager", "goals"]:
        goal_id, action = parts[4], parts[5]
        if method == "POST" and action == "activate":
            return manager.activate_goal(goal_id)
        if method == "POST" and action == "pause":
            return manager.pause_goal(goal_id)
        if method == "POST" and action == "complete":
            return manager.complete_goal(goal_id)
    if len(parts) == 6 and parts[:4] == ["api", "v1", "manager", "operation-tasks"]:
        operation_task_id, action = parts[4], parts[5]
        if method == "POST" and action == "claim":
            return manager.claim_operation_task(operation_task_id, body)
        if method == "POST" and action == "plan":
            return manager.record_operation_plan(operation_task_id, body)
        if method == "POST" and action == "finish":
            return manager.finish_operation_task(operation_task_id, body)
    if (
        len(parts) == 6
        and parts[:4] == ["api", "v1", "manager", "operation-reports"]
        and method == "POST"
        and parts[5] == "revisions"
    ):
        return manager.revise_operation_report(parts[4], body)
    raise ServiceError("NOT_FOUND", "经理机接口不存在", 404)


def _require_manager_service(
    manager_service: ManagerService | None,
) -> ManagerService:
    if manager_service is None:
        raise ServiceError("MANAGER_SERVICE_UNAVAILABLE", "经理机服务未启用", 503)
    return manager_service


def serve(host: str = "0.0.0.0", port: int = 8765) -> None:
    host = _validate_bind_host(host)
    project_root = Path(__file__).resolve().parent.parent
    service = ApplicationService(
        webui_restarter=lambda: schedule_webui_restart(
            python_executable=sys.executable,
            project_root=project_root,
            host=host,
            port=port,
        )
    )
    manager_service = ManagerService()
    server = LocalThreadingHTTPServer(
        (host, port),
        make_handler(service, manager_service=manager_service),
    )
    manager_sync = ManagerSyncService(manager_service)
    if manager_service.store.list("machines"):
        from manager_sync_log import start_log_window

        start_log_window(manager_sync.log_path, sys.executable)
    manager_agent = ManagerAgentService(manager_service)
    manager_sync.start()
    manager_agent.start()
    print(f"auto-xhs WebUI: http://127.0.0.1:{server.server_port}")
    if host == "0.0.0.0":
        print(f"局域网访问：http://<经理机局域网 IP>:{server.server_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager_agent.stop()
        manager_sync.stop()
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动 auto-xhs 本地 WebUI")
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        choices=["0.0.0.0", "127.0.0.1"],
        help="0.0.0.0 支持本机及局域网访问；127.0.0.1 仅本机访问",
    )
    parser.add_argument("--port", type=int, default=8765, help="本地 WebUI 端口")
    return parser


if __name__ == "__main__":
    args = build_parser().parse_args()
    serve(args.host, args.port)
