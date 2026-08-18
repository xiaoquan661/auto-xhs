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
    _dispatch_api,
    _dispatch_mutation,
    _validate_bind_host,
    make_handler,
)
from scripts.web_session import SESSION_HEADER


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

    def get_account_autostart(self, account: str) -> dict:
        return {"success": True, "autostart": {"account": account, "enabled": False}}

    def set_account_autostart(self, account: str, **body) -> dict:
        return {"success": True, "autostart": {"account": account, **body}}

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

    def list_records(self) -> dict:
        return {"success": True, "records": []}

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

    assert paused["global_paused"] is True
    assert task["task"]["capability"] == "search-feeds"
    assert draft["draft"]["content"] == "新文本"
    assert diagnostics["path"] == "diagnostic.json"
    assert retried["task"]["state"] == "SUCCESS"
    assert cancelled["task"]["state"] == "CANCELLED"


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


def test_webui_contains_account_setup_and_product_navigation() -> None:
    web_root = Path(__file__).resolve().parents[1] / "webui"
    html = (web_root / "index.html").read_text(encoding="utf-8")
    script = (web_root / "app.js").read_text(encoding="utf-8")

    for label in ("总览", "账号状态", "任务与确认", "执行记录", "诊断设置"):
        assert label in html
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
    assert "进入切换账号流程" in script
    assert "pairing/begin" in script
    assert "identity/check" in script
    assert "switch/begin" in script
    assert "switch/complete" in script
    assert "switch/cancel" in script
    assert "auth/logout" in script
    assert "退出当前账号" in script
    assert "回读页面确认已退出" in script
    assert 'id="account-remove-dialog"' in html
    assert "删除槽位" in script
    assert '"DELETE"' in script
    assert "切换登录账号" in html
    assert "切换账号" in script
    assert "tasks/${created.task.task_id}/execute" in script
    assert "tasks/${task.task_id}/${action}" in script
    assert 'id="task-submit"' in html
    assert "account-activity" in script
    assert "pendingSubmissionByAccount" in script
    assert "pollTaskActivity" in script
    assert "pollAccountStatuses" in script
    assert "active ? 1000 : 3000" in script
    assert "可选择其他已就绪且空闲的账号并行执行" in script
    for capability in ("browse-feeds", "search-feeds", "get-feed-detail", "user-profile", "like-feed", "favorite-feed", "keyword-engagement"):
        assert f'"{capability}"' in script
    assert 'engagement: ["keyword-engagement"]' in script
    assert 'id="task-undo"' not in html
    assert 'id="task-engagement-action"' in html
    assert 'id="task-engagement-count"' in html
    assert 'id="task-candidate-pool"' in html
    assert 'id="task-collection-minutes"' in html
    assert "collection_stop_reason" in script
    for template in ("browse", "search", "analysis", "engagement"):
        assert f'value="{template}"' in html
    assert 'id="task-duration"' in html
    assert 'id="task-count"' in html
    assert 'id="task-tab-immediate"' in html
    assert 'id="task-tab-confirmation"' in html
    assert 'id="task-template"' in html
    assert "renderTaskTemplateActions" in script
    assert "showTaskPanel" in script
    assert "appendBrowseResults" in script
    assert "appendTaskResult" in script
    assert "resultByTask" in script
    assert "立即任务" in html
    assert 'id="task-tab-plan"' not in html
    assert 'id="task-panel-plan"' not in html
    assert "批量／定时计划" not in html
    assert "评论／回复确认" in html
    assert 'id="record-list"' in html
    assert 'id="draft-form"' in html
    assert html.index('value="post-comment"') < html.index('value="reply-comment"')
    assert 'id="draft-action-help"' in html
    assert 'id="draft-generate"' in html
    assert 'id="draft-target-label">目标笔记 ID' in html
    assert "function updateDraftFields" in script
    assert "function generateCommentDraft" in script
    assert "function useResultAsComment" not in script
    assert '$("#draft-action").value = "post-comment"' in script
    assert 'draft.action_type === "post-comment"' in script
    assert '<option value="comment">随机评论</option>' in html
    assert 'id="random-comment-settings"' in html
    assert 'id="task-comment-count"' in html
    assert "direct_send_authorized: true" in script
    assert "function appendRandomCommentResults" in script
    assert "创建并直接发送" in script
    assert "drafts/${draft.draft_id}/execute" in script
    assert 'id="global-pause"' in html
    assert 'id="settings-form"' in html
    assert "diagnostics/export" in script
    assert "bridge/${action}" in script
    assert "account.extension_dir" in script


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
            assert "先处理需要关注的账号" in response.read().decode("utf-8")
            assert response.headers[SESSION_HEADER] == token

        with urlopen(f"{base_url}/styles.css", timeout=3) as response:
            assert ".overview" in response.read().decode("utf-8")

        with urlopen(f"{base_url}/app.js", timeout=3) as response:
            assert "loadDashboard" in response.read().decode("utf-8")

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
