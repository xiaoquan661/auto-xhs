from __future__ import annotations

import json
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from scripts.application_service import ServiceError
from scripts.web_server import (
    LocalThreadingHTTPServer,
    _dispatch_api,
    _dispatch_mutation,
    _validate_bind_host,
    make_handler,
)
from scripts.web_session import SESSION_HEADER


def test_web_server_uses_exclusive_local_port() -> None:
    assert LocalThreadingHTTPServer.allow_reuse_address is False
    assert LocalThreadingHTTPServer.allow_reuse_port is False


class FakeService:
    def __init__(self) -> None:
        self.paused = False
        self.tasks = []

    def health(self) -> dict:
        return {"success": True, "status": "ok"}

    def list_capabilities(self) -> dict:
        return {
            "success": True,
            "capabilities": [],
            "summary": {"total": 0, "enabled_in_v1": 0},
        }

    def list_accounts(self) -> dict:
        return {"success": True, "accounts": [{"name": "alpha"}]}

    def doctor_account(self, account=None) -> dict:
        return {"success": True, "account": account, "healthy": True}

    def get_account_status(self, account: str) -> dict:
        if account == "missing":
            raise ServiceError("ACCOUNT_NOT_FOUND", "账号不存在", 404)
        return {"success": True, "account": {"name": account}, "status": "BLOCKED"}

    def system_status(self) -> dict:
        return {
            "success": True,
            "global_paused": self.paused,
            "global_concurrency": 3,
            "product_version": "test",
            "l1_limits": {
                "hourly": 20,
                "daily": 100,
                "dedup_minutes": 10,
                "failure_threshold": 3,
            },
            "summary": {
                "tasks_total": len(self.tasks),
                "drafts_waiting": 0,
                "recent_failures": 0,
            },
        }

    def set_global_pause(self, paused: bool) -> dict:
        self.paused = paused
        return {"success": True, "global_paused": paused}

    def restart_local_service(self) -> dict:
        return {"success": True, "status": "restart_scheduled"}

    def update_system_settings(self, **body) -> dict:
        return {"success": True, **body}

    def export_diagnostics(self) -> dict:
        return {"success": True, "path": "diagnostic.json"}

    def get_bridge_status(self, account: str) -> dict:
        return {"success": True, "lifecycle": {"account": account}}

    def start_account_bridge(self, account: str) -> dict:
        return {"success": True, "lifecycle": {"account": account, "started": True}}

    def start_account_bridge_only(self, account: str) -> dict:
        return {
            "success": True,
            "lifecycle": {"account": account, "bridge_running": True},
        }

    def stop_account_bridge(self, account: str) -> dict:
        return {"success": True, "lifecycle": {"account": account, "started": False}}

    def restart_account_bridge(self, account: str) -> dict:
        return {"success": True, "lifecycle": {"account": account, "restarted": True}}

    def list_tasks(self) -> dict:
        return {"success": True, "tasks": self.tasks}

    def get_task(self, task_id: str) -> dict:
        return {"success": True, "task": {"task_id": task_id}}

    def execute_task(self, task_id: str) -> dict:
        return {"success": True, "task": {"task_id": task_id, "state": "SUCCESS"}}

    def retry_task(self, task_id: str) -> dict:
        return {"success": True, "task": {"task_id": task_id, "state": "SUCCESS"}}

    def cancel_task(self, task_id: str) -> dict:
        return {"success": True, "task": {"task_id": task_id, "state": "CANCELLED"}}

    def confirm_publish_task(self, task_id: str, **body) -> dict:
        return {"success": True, "task": {"task_id": task_id, "state": "SUCCESS", **body}}

    def save_publish_task_as_draft(self, task_id: str, **body) -> dict:
        return {"success": True, "task": {"task_id": task_id, "state": "CANCELLED", **body}}

    def list_records(self) -> dict:
        return {"success": True, "records": []}

    def list_inbound_events(self) -> dict:
        return {"success": True, "events": []}

    def get_inbound_event(self, event_id: str) -> dict:
        return {"success": True, "event": {"event_id": event_id}}

    def create_passive_reply_draft(self, event_id: str, **body) -> dict:
        return {
            "success": True,
            "event": {"event_id": event_id},
            "draft": body,
        }

    def intelligent_reply_status(self) -> dict:
        return {
            "success": True,
            "configured": True,
            "model": "fake-reply-model",
            "missing": [],
            "mode": "review_only",
        }

    def update_reply_model_settings(self, **body) -> dict:
        return {
            "success": True,
            "configured": True,
            "model": body["model"],
            "base_url": body["base_url"],
            "api_key_saved": bool(body.get("api_key")),
        }

    def test_reply_model_connection(self) -> dict:
        return {
            "success": True,
            "model": "fake-reply-model",
            "message": "模型连接成功",
        }

    def create_intelligent_reply_draft(self, event_id: str, **body) -> dict:
        return {
            "success": True,
            "event": {"event_id": event_id},
            "generation": {"reply": "AI 回复草稿", "mode": "review_only"},
            "draft": body,
        }

    def list_operation_events(self) -> dict:
        return {"success": True, "events": []}

    def data_recovery_summary(self) -> dict:
        return {
            "success": True,
            "summary": {
                "published_posts": {"total": 0, "states": {}},
                "operation_records": {"total": 0, "states": {}},
                "reply_records": {"total": 0, "states": {}},
            },
        }

    def list_published_posts(self) -> dict:
        return {"success": True, "posts": []}

    def get_published_post(self, published_post_id: str) -> dict:
        return {
            "success": True,
            "post": {"published_post_id": published_post_id},
        }

    def list_operation_records(self) -> dict:
        return {"success": True, "records": []}

    def list_reply_records(self) -> dict:
        return {"success": True, "records": []}

    def get_reply_record(self, reply_record_id: str) -> dict:
        return {"success": True, "record": {"reply_record_id": reply_record_id}}

    def get_metric_history(self, account: str, entity_type: str, entity_id: str) -> dict:
        return {
            "success": True,
            "snapshots": [{"account": account, "type": entity_type, "id": entity_id}],
        }

    def get_metric_delta(self, account: str, entity_type: str, entity_id: str) -> dict:
        return {
            "success": True,
            "delta": {"account": account, "type": entity_type, "id": entity_id},
        }

    def list_operation_reports(self) -> dict:
        return {"success": True, "reports": []}

    def get_operation_report(self, report_id: str) -> dict:
        return {"success": True, "report": {"report_id": report_id}}

    def generate_operation_report(self, **body) -> dict:
        return {"success": True, "report": {"report_id": "report-1", **body}}

    def revise_operation_report(self, report_id: str, **body) -> dict:
        return {
            "success": True,
            "report": {"report_id": "report-2", "previous_report_id": report_id, **body},
        }

    def list_reply_rules(self) -> dict:
        return {"success": True, "rules": []}

    def create_reply_rule(self, **body) -> dict:
        return {"success": True, "rule": body}

    def update_reply_rule(self, rule_id: str, **body) -> dict:
        return {"success": True, "rule": {"rule_id": rule_id, **body}}

    def set_reply_rule_enabled(self, rule_id: str, **body) -> dict:
        return {"success": True, "rule": {"rule_id": rule_id, **body}}

    def list_reply_cycles(self) -> dict:
        return {"success": True, "cycles": []}

    def list_reply_decisions(self) -> dict:
        return {"success": True, "decisions": []}

    def run_reply_cycle(self, **body) -> dict:
        return {
            "success": True,
            "cycle": {"cycle_id": "cycle-1", "summary": body},
        }

    def create_task(self, **body) -> dict:
        self.tasks.append(body)
        return {"success": True, "task": body}

    def list_drafts(self) -> dict:
        return {"success": True, "drafts": []}

    def create_draft(self, **body) -> dict:
        return {"success": True, "draft": body}

    def update_draft(self, draft_id: str, **body) -> dict:
        return {"success": True, "draft": {"draft_id": draft_id, **body}}

    def confirm_draft(self, draft_id: str, *, ttl_seconds: int) -> dict:
        return {
            "success": True,
            "approval": {"draft_id": draft_id, "ttl_seconds": ttl_seconds},
        }

    def execute_draft(self, draft_id: str, **body) -> dict:
        return {"success": True, "task": {"draft_id": draft_id, **body}}

    def discover_profiles(self, user_data_dir=None) -> dict:
        return {"success": True, "profiles": [], "user_data_dir": user_data_dir}

    def create_account_slot(self, **body) -> dict:
        return {"success": True, "account": body}

    def import_account_slot(self, **body) -> dict:
        return {"success": True, "account": body}

    def remove_account_slot(self, account: str, **body) -> dict:
        return {"success": True, "account": account, "archived": True, **body}

    def begin_account_pairing(self, account: str, **body) -> dict:
        return {"success": True, "pairing": {"account": account, **body}}

    def begin_account_setup(self, account: str, **body) -> dict:
        return {"success": True, "setup": {"account": account, "phase": "WAITING_PAIRING", **body}}

    def account_pairing_status(self, account: str) -> dict:
        return {"success": True, "pairing": {"account": account}}

    def check_account_identity(self, account: str) -> dict:
        return {"success": True, "account": account, "identity": {"user_id": "uid"}}

    def record_account_identity(self, account: str, **body) -> dict:
        return {"success": True, "identity": {"account": account, **body}}

    def replace_account_identity(self, account: str, **body) -> dict:
        return {
            "success": True,
            "identity": {"account": account, "event": "identity-replaced", **body},
        }

    def get_account_switch(self, account: str) -> dict:
        return {"success": True, "account": account, "pending": None, "history": []}

    def begin_account_switch(self, account: str, **body) -> dict:
        return {"success": True, "account": account, "switch": {"status": "awaiting_login", **body}}

    def complete_account_switch(self, account: str, **body) -> dict:
        return {"success": True, "account": account, "switch": {"event": "login-switched", **body}}

    def cancel_account_switch(self, account: str, **body) -> dict:
        return {"success": True, "account": account, "switch": {"cancelled": True, **body}}

    def logout_account(self, account: str, **body) -> dict:
        return {"success": True, "account": account, "logged_out": True, **body}


def test_web_server_refuses_non_loopback_bind() -> None:
    assert _validate_bind_host("127.0.0.1") == "127.0.0.1"
    with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
        _validate_bind_host("0.0.0.0")


def test_api_dispatch_routes_to_shared_service() -> None:
    service = FakeService()

    assert _dispatch_api(service, "/api/v1/health")["status"] == "ok"
    assert _dispatch_api(service, "/api/v1/accounts")["accounts"][0]["name"] == "alpha"
    assert _dispatch_api(service, "/api/v1/tasks")["tasks"] == []
    assert _dispatch_api(service, "/api/v1/inbound-events")["events"] == []
    assert _dispatch_api(service, "/api/v1/operation-events")["events"] == []
    assert _dispatch_api(service, "/api/v1/data-recovery/summary")["summary"][
        "published_posts"
    ]["total"] == 0
    assert _dispatch_api(service, "/api/v1/data-recovery/published-posts")["posts"] == []
    assert _dispatch_api(service, "/api/v1/data-recovery/operation-records")["records"] == []
    assert _dispatch_api(service, "/api/v1/data-recovery/reply-records")["records"] == []
    assert _dispatch_api(
        service,
        "/api/v1/data-recovery/published-posts/post-1",
    )["post"]["published_post_id"] == "post-1"
    assert _dispatch_api(
        service,
        "/api/v1/data-recovery/reply-records/reply-1",
    )["record"]["reply_record_id"] == "reply-1"
    assert _dispatch_api(service, "/api/v1/reply-rules")["rules"] == []
    assert _dispatch_api(service, "/api/v1/reply-cycles")["cycles"] == []
    assert _dispatch_api(service, "/api/v1/reply-decisions")["decisions"] == []
    assert _dispatch_api(service, "/api/v1/operation-reports")["reports"] == []
    assert (
        _dispatch_api(service, "/api/v1/operation-reports/report-1")["report"][
            "report_id"
        ]
        == "report-1"
    )
    assert (
        _dispatch_api(service, "/api/v1/reply-intelligence/status")["mode"]
        == "review_only"
    )
    assert (
        _dispatch_api(service, "/api/v1/inbound-events/event-1")["event"]["event_id"]
        == "event-1"
    )
    assert (
        _dispatch_api(service, "/api/v1/metrics/alpha/note/note-1")["snapshots"][0][
            "id"
        ]
        == "note-1"
    )
    assert (
        _dispatch_api(service, "/api/v1/metrics/alpha/note/note-1/delta")["delta"][
            "id"
        ]
        == "note-1"
    )
    assert (
        _dispatch_api(service, "/api/v1/accounts/alpha/status")["account"]["name"]
        == "alpha"
    )
    assert _dispatch_api(service, "/api/v1/accounts/alpha/switch")["pending"] is None
    with pytest.raises(ServiceError) as exc_info:
        _dispatch_api(service, "/api/v1/unknown")
    assert exc_info.value.code == "NOT_FOUND"


def test_mutation_dispatch_routes_to_shared_service() -> None:
    service = FakeService()

    paused = _dispatch_mutation(service, "POST", "/api/v1/system/pause", {})
    restarted = _dispatch_mutation(service, "POST", "/api/v1/system/restart", {})
    task = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/tasks",
        {"source": "webui", "capability": "search-feeds"},
    )
    draft = _dispatch_mutation(
        service,
        "PATCH",
        "/api/v1/drafts/draft-1",
        {"content": "新文本"},
    )
    diagnostics = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/diagnostics/export",
        {},
    )
    passive = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/inbound-events/event-1/reply-draft",
        {"verified_uid": "owner-1", "content": "回复文本"},
    )
    intelligent = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/inbound-events/event-2/intelligent-reply-draft",
        {"account_slot": "alpha", "verified_uid": "owner-1"},
    )
    model_settings = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/reply-intelligence/settings",
        {
            "confirmed": True,
            "api_key": "test-key",
            "base_url": "https://llm.example/v1",
            "model": "reply-model",
        },
    )
    model_test = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/reply-intelligence/test",
        {},
    )
    reply_rule = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/reply-rules",
        {"account_slot": "alpha"},
    )
    updated_rule = _dispatch_mutation(
        service,
        "PATCH",
        "/api/v1/reply-rules/rule-1",
        {"reply_style": "professional"},
    )
    enabled_rule = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/reply-rules/rule-1/enabled",
        {"enabled": True},
    )
    reply_cycle = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/reply-cycle",
        {"account_slot": "alpha", "max_events": 10},
    )
    report = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/operation-reports",
        {
            "report_type": "daily",
            "account_slot": "alpha",
            "period_start": "2026-08-28T00:00:00+00:00",
            "period_end": "2026-08-28T23:59:59+00:00",
        },
    )
    revision = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/operation-reports/report-1/revisions",
        {"revision_reason": "补录指标"},
    )
    retried = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/tasks/task-1/retry",
        {},
    )
    cancelled = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/tasks/task-2/cancel",
        {},
    )
    published = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/tasks/publish-1/publish-confirm",
        {
            "account_slot": "alpha",
            "verified_uid": "owner-1",
            "confirmed": True,
            "preview_confirmed": True,
            "preview_revision": "preview-1",
        },
    )
    saved_draft = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/tasks/publish-2/save-draft",
        {
            "account_slot": "alpha",
            "verified_uid": "owner-1",
            "confirmed": True,
            "preview_revision": "preview-2",
        },
    )

    assert paused["global_paused"] is True
    assert restarted["status"] == "restart_scheduled"
    assert task["task"]["capability"] == "search-feeds"
    assert draft["draft"]["content"] == "新文本"
    assert diagnostics["path"] == "diagnostic.json"
    assert passive["event"]["event_id"] == "event-1"
    assert passive["draft"]["content"] == "回复文本"
    assert intelligent["generation"]["reply"] == "AI 回复草稿"
    assert model_settings["configured"] is True
    assert model_settings["model"] == "reply-model"
    assert model_test["message"] == "模型连接成功"
    assert reply_rule["rule"]["account_slot"] == "alpha"
    assert updated_rule["rule"]["reply_style"] == "professional"
    assert enabled_rule["rule"]["enabled"] is True
    assert reply_cycle["cycle"]["summary"]["account_slot"] == "alpha"
    assert report["report"]["report_id"] == "report-1"
    assert revision["report"]["previous_report_id"] == "report-1"
    assert retried["task"]["state"] == "SUCCESS"
    assert cancelled["task"]["state"] == "CANCELLED"
    assert published["task"]["state"] == "SUCCESS"
    assert published["task"]["preview_confirmed"] is True
    assert saved_draft["task"]["state"] == "CANCELLED"


def test_account_setup_api_routes_to_shared_service() -> None:
    service = FakeService()

    discovered = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/discover",
        {"user_data_dir": r"C:\Chrome\User Data"},
    )
    imported = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/import",
        {"name": "alpha", "confirmed": True},
    )
    pairing = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/pairing/begin",
        {"confirmed": True},
    )
    setup = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/setup/begin",
        {"confirmed": True},
    )
    identity = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/identity/check",
        {},
    )
    identity_replaced = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/identity/replace",
        {
            "confirmed": True,
            "expected_recorded_user_id": "old-uid",
            "expected_observed_user_id": "new-uid",
        },
    )
    switch_begin = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/switch/begin",
        {"confirmed": True},
    )
    switch_complete = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/switch/complete",
        {"confirmed": True},
    )
    switch_cancel = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/switch/cancel",
        {"confirmed": True},
    )
    logout = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/auth/logout",
        {"confirmed": True},
    )
    removed = _dispatch_mutation(
        service,
        "DELETE",
        "/api/v1/accounts/alpha",
        {"confirmed": True, "confirmation_name": "alpha"},
    )
    bridge = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/bridge/restart",
        {},
    )
    bridge_only = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/accounts/alpha/bridge/start-only",
        {},
    )

    assert discovered["user_data_dir"].endswith("User Data")
    assert imported["account"]["name"] == "alpha"
    assert pairing["pairing"]["account"] == "alpha"
    assert setup["setup"]["phase"] == "WAITING_PAIRING"
    assert identity["identity"]["user_id"] == "uid"
    assert identity_replaced["identity"]["event"] == "identity-replaced"
    assert switch_begin["switch"]["status"] == "awaiting_login"
    assert switch_complete["switch"]["event"] == "login-switched"
    assert switch_cancel["switch"]["cancelled"] is True
    assert logout["logged_out"] is True
    assert logout["confirmed"] is True
    assert removed["archived"] is True
    assert removed["confirmation_name"] == "alpha"
    assert bridge["lifecycle"]["restarted"] is True
    assert bridge_only["lifecycle"]["bridge_running"] is True


def test_draft_execute_api_routes_to_shared_service() -> None:
    service = FakeService()

    result = _dispatch_mutation(
        service,
        "POST",
        "/api/v1/drafts/draft-1/execute",
        {"approval_id": "approval-1", "feed_id": "feed-1", "xsec_token": "token"},
    )

    assert result["task"]["draft_id"] == "draft-1"
    assert result["task"]["approval_id"] == "approval-1"


def test_webui_contains_manager_operations_and_account_workspace() -> None:
    web_root = Path(__file__).resolve().parents[1] / "webui"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    controller_script = (web_root / "manager-console.js").read_text(encoding="utf-8")

    assert "运营总览" in html
    assert "员工机" in html
    assert "账号管理" in html
    assert 'id="manager-executions-panel"' in html
    assert "数据回收" in html
    assert "异常中心" in html
    assert 'id="manager-operation-detail-dialog"' in html
    assert 'id="manager-account-node-list"' in html
    assert 'src="/manager-console.js' in html
    assert 'href="/workspace.css' in html
    assert "dashboard-snapshot" in controller_script
    assert "function renderAccountWorkspace()" in controller_script


def _legacy_webui_contains_account_setup_and_product_navigation() -> None:
    web_root = Path(__file__).resolve().parents[1] / "webui"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    controller_script = (web_root / "app.js").read_text(encoding="utf-8")
    catalog_script = (web_root / "task-catalog.js").read_text(encoding="utf-8")
    script = f"{catalog_script}\n{controller_script}"
    workspace_script = (web_root / "workspace.js").read_text(encoding="utf-8")

    for label in (
        "工作台",
        "账号管理",
        "执行记录",
        "消息互动",
        "发布管理",
        "数据分析",
        "系统设置",
    ):
        assert label in html
    for workspace in (
        "overview",
        "accounts",
        "records",
        "messages",
        "publishing",
        "data",
        "system",
    ):
        assert f'data-workspace="{workspace}"' in html
        assert f'data-workspace-link="{workspace}"' in html
    assert 'data-workspace="tasks"' not in html
    assert 'data-workspace="approvals"' not in html
    assert 'data-workspace-link="tasks"' not in html
    assert 'data-workspace-link="approvals"' not in html
    nav_positions = [
        html.index(f'data-workspace-link="{workspace}"')
        for workspace in (
            "overview",
            "accounts",
            "records",
            "messages",
            "publishing",
            "data",
            "system",
        )
    ]
    assert nav_positions == sorted(nav_positions)
    assert "hashchange" in workspace_script
    assert "window.AutoXhsWorkspace" in workspace_script
    assert 'tasks: "overview"' in workspace_script
    assert 'approvals: "messages"' in workspace_script
    assert 'id="account-create-panel" hidden' in html
    assert 'id="open-account-create"' in html
    assert 'id="account-form"' in html
    assert 'id="account-setup-dialog"' in html
    assert "accounts/import" in script
    assert "setup/begin" in script
    assert "连接调试" in script
    assert "启动 Bridge" in script
    assert "停止 Bridge" in script
    assert "bridge/${action}" in script
    assert "重新尝试" in script
    assert "重新生成配对信息" in script
    assert "我已在扩展确认，立即检测" in script
    assert "我已登录，立即检测" in script
    assert "系统已停止自动检测" in script
    assert "处理账号不一致" in script
    assert 'id="identity-mismatch-dialog"' in html
    assert "identity/replace" in script
    assert "我登错了，退出当前账号" in html
    assert "确认覆盖槽位 UID" in html
    assert "pairing/begin" in script
    assert "identity/check" in script
    assert "switch/begin" in script
    assert "switch/complete" in script
    assert "switch/cancel" in script
    assert "auth/logout" in script
    assert "退出当前账号" in script
    assert "回读页面确认已退出" in script
    assert "退出成功，" in script
    assert "当前无账号" in script
    assert "我已登录，完成核验" in script
    assert "终止当前任务" in script
    assert "取消等待" in script
    assert 'window.AutoXhsWorkspace?.navigate("records"' in script
    assert 'id="account-remove-dialog"' in html
    assert "删除槽位" in script
    assert '"DELETE"' in script
    assert "切换登录账号" in html
    assert "切换账号" in script
    assert "tasks/${created.task.task_id}/execute" in script
    assert "tasks/${task.task_id}/${action}" in script
    assert 'id="published-post-list"' in html
    assert 'id="reply-record-list"' in html
    assert "data-recovery/published-posts" in script
    assert "data-recovery/reply-records" in script
    assert 'id="operation-record-list"' not in html
    assert "data-recovery/operation-records" not in script
    assert 'id="task-submit"' not in html
    assert "account-activity" in script
    assert "pendingSubmissionByAccount" in script
    assert "pollTaskActivity" in script
    assert "pollAccountStatuses" in script
    assert "active ? 1000 : 3000" in script
    assert "async function submitTask" not in script
    assert 'id="task-undo"' not in html
    assert 'id="task-engagement-action"' not in html
    assert 'id="task-engagement-count"' not in html
    assert 'id="task-candidate-pool"' not in html
    assert 'id="task-collection-minutes"' not in html
    assert "collection_stop_reason" in script
    for template in ("browse", "search", "analysis", "engagement"):
        assert f'value="{template}"' not in html
    assert 'id="task-duration"' not in html
    assert 'id="task-count"' not in html
    assert 'id="task-tab-immediate"' not in html
    assert 'id="task-tab-confirmation"' not in html
    assert 'id="task-template"' not in html
    assert "renderTaskTemplateActions" not in script
    assert "showTaskPanel" in script
    assert "appendBrowseResults" in script
    assert "appendTaskResult" in script
    assert "resultByTask" in script
    assert 'id="task-launch"' not in html
    assert 'id="open-task-launch"' not in html
    assert 'data-workspace="messages"' in html
    assert 'data-workspace="publishing"' in html
    assert 'id="task-tab-plan"' not in html
    assert 'id="task-panel-plan"' not in html
    assert "批量／定时计划" not in html
    assert "评论与私信" in html
    assert "新评论处理状态" in html
    assert 'id="intelligent-reply-list"' in html
    assert 'id="comment-collection-form" hidden' in html
    assert 'id="comment-collection-account"' in html
    assert 'id="collect-comments"' in html
    assert 'id="record-list"' in html
    assert 'id="draft-form" hidden' in html
    assert 'id="run-reply-cycle" hidden' in html
    assert '$("#task-form").addEventListener("submit", submitTask)' not in script
    assert '$("#draft-form").addEventListener("submit", submitDraft)' not in script
    assert '$("#run-reply-cycle").addEventListener("click", runReplyCycle)' not in script
    assert html.index('value="post-comment"') < html.index('value="reply-comment"')
    assert 'id="draft-action-help"' in html
    assert 'id="draft-generate"' in html
    assert 'id="draft-target-label">目标笔记 ID' in html
    assert "function updateDraftFields" in script
    assert "function generateCommentDraft" in script
    assert "function generateIntelligentReplyDraft" in script
    assert "function collectNewComments" in script
    assert 'capability: "collect-note-comments"' in script
    assert "commentCollectionSummary" in script
    assert "reply-intelligence/status" in script
    assert "intelligent-reply-draft" in script
    assert "function useResultAsComment" not in script
    assert '$("#draft-action").value = "post-comment"' in script
    assert 'draft.action_type === "post-comment"' in script
    assert '<option value="comment">随机评论</option>' not in html
    assert 'id="random-comment-settings"' not in html
    assert 'id="home-engagement-settings"' not in html
    assert 'id="task-comment-count"' not in html
    assert 'id="task-home-comment-count"' not in html
    assert 'id="task-home-style"' not in html
    assert "homeCommentCount" not in script
    assert "homeStyle" not in script
    assert "direct_send_authorized: true" not in script
    assert "function appendRandomCommentResults" in script
    assert "创建并直接发送" not in script
    assert "drafts/${draft.draft_id}/execute" in script
    assert 'id="global-pause"' in html
    assert 'id="restart-service"' in html
    assert "function restartLocalService" in script
    assert "runtime_instance_id !== previousInstanceId" in script
    assert 'id="settings-form"' in html
    assert 'id="reply-model-form"' in html
    assert 'id="reply-model-api-key"' in html
    assert "reply-intelligence/settings" in script
    assert "reply-intelligence/test" in script
    assert "diagnostics/export" in script
    assert "bridge/${action}" in script
    assert "account.extension_dir" in script
    assert 'id="node-address"' in html
    assert 'id="node-running-total"' in html
    assert 'id="execution-filter-form"' in html
    assert 'id="task-list"' in html
    assert 'id="publish-task-list"' in html
    assert 'id="operation-report-form"' in html
    assert 'id="operation-report-list"' in html
    assert "operation-reports" in script
    for panel in ("drafts", "comments", "private-messages", "rules"):
        assert f'data-subpage-target="{panel}"' in html
        assert f'data-subpage-name="{panel}"' in html
    for panel in ("ledgers", "reports"):
        assert f'data-subpage-target="{panel}"' in html
        assert f'data-subpage-name="{panel}"' in html
    assert "function showSubpage" in script


def test_webui_stops_guided_uid_polling_when_profile_is_logged_out() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "webui" / "app.js"
    ).read_text(encoding="utf-8")

    login_required_branch = script.split(
        'if (error.code === "LOGIN_REQUIRED") {', 1
    )[1].split('if (error.code === "IDENTITY_UID_UNAVAILABLE") {', 1)[0]
    uid_unavailable_branch = script.split(
        'if (error.code === "IDENTITY_UID_UNAVAILABLE") {', 1
    )[1].split('$("#account-setup-stage").textContent = "身份检测失败";', 1)[0]

    assert "accountSetupPollTimer = null" in login_required_branch
    assert "scheduleAccountSetupPoll" not in login_required_branch
    assert "系统已停止自动检测" in login_required_branch
    assert "scheduleAccountSetupPoll(inspectGuidedIdentity, 2000)" in uid_unavailable_branch


def test_webui_renders_logged_out_identity_only_inside_account_card() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "webui" / "app.js"
    ).read_text(encoding="utf-8")

    activity_renderer = script.split("function paintAccountActivity", 1)[1].split(
        "function syncAccountActivityUI", 1
    )[0]
    account_renderer = script.split("function accountCard", 1)[1].split(
        "const setupSteps", 1
    )[0]

    assert "LOGIN_REQUIRED" not in activity_renderer
    assert 'else if (state === "LOGIN_REQUIRED") {' in account_renderer
    assert 'bindingRow("当前小红书账号", "当前无账号"' in account_renderer


def test_webui_account_card_exposes_task_management_actions() -> None:
    script = (
        Path(__file__).resolve().parents[1] / "webui" / "app.js"
    ).read_text(encoding="utf-8")
    activity_renderer = script.split("function paintAccountActivity", 1)[1].split(
        "function syncAccountActivityUI", 1
    )[0]

    assert 'view.textContent = "查看任务"' in activity_renderer
    assert 'cancel.textContent = activity.state === "RUNNING"' in activity_renderer
    assert '"终止当前任务"' in activity_renderer
    assert 'updateTask(activity, "cancel", cancel)' in activity_renderer
    assert 'activity.state === "CANCEL_REQUESTED"' in activity_renderer


def test_http_server_serves_api_static_ui_and_protects_mutations() -> None:
    token = "test-local-session"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(FakeService(), session_token=token),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(f"{base_url}/api/v1/health", timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
            assert response.status == 200
            assert payload["status"] == "ok"

        with urlopen(f"{base_url}/", timeout=3) as response:
            assert "经理机运营控制台" in response.read().decode("utf-8")
            assert response.headers[SESSION_HEADER] == token

        with urlopen(f"{base_url}/workspace.css", timeout=3) as response:
            assert ".manager-account-layout" in response.read().decode("utf-8")

        with urlopen(f"{base_url}/manager-console.js", timeout=3) as response:
            assert "dashboard-snapshot" in response.read().decode("utf-8")

        with urlopen(f"{base_url}/api-client.js", timeout=3) as response:
            assert "AutoXhsApi" in response.read().decode("utf-8")

        request = Request(f"{base_url}/api/v1/accounts", method="POST")
        with pytest.raises(HTTPError) as exc_info:
            urlopen(request, timeout=3)
        assert exc_info.value.code == 403
        error = json.loads(exc_info.value.read().decode("utf-8"))
        assert error["error"]["code"] == "SESSION_REQUIRED"

        body = json.dumps({"source": "webui", "capability": "search-feeds"}).encode()
        request = Request(
            f"{base_url}/api/v1/tasks",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", SESSION_HEADER: token},
        )
        with urlopen(request, timeout=3) as response:
            task = json.loads(response.read().decode("utf-8"))
        assert task["task"]["capability"] == "search-feeds"

        body = json.dumps(
            {"confirmed": True, "confirmation_name": "alpha"}
        ).encode()
        request = Request(
            f"{base_url}/api/v1/accounts/alpha",
            data=body,
            method="DELETE",
            headers={"Content-Type": "application/json", SESSION_HEADER: token},
        )
        with urlopen(request, timeout=3) as response:
            removed = json.loads(response.read().decode("utf-8"))
        assert removed["archived"] is True
        assert removed["confirmation_name"] == "alpha"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_http_server_maps_service_errors_to_json(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(FakeService(), web_root=tmp_path),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/v1/accounts/missing/status"
        with pytest.raises(HTTPError) as exc_info:
            urlopen(url, timeout=3)
        payload = json.loads(exc_info.value.read().decode("utf-8"))
        assert exc_info.value.code == 404
        assert payload["error"]["code"] == "ACCOUNT_NOT_FOUND"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
