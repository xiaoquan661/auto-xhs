from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"


def _source(name: str) -> str:
    return (WEBUI / name).read_text(encoding="utf-8")


def test_manager_console_exposes_seven_product_views() -> None:
    html = _source("index.html")

    for view, label in (
        ("overview", "运营总览"),
        ("operations", "运营任务"),
        ("workers", "员工机"),
        ("accounts", "账号与人设"),
        ("results", "数据回收"),
        ("exceptions", "异常中心"),
        ("settings", "系统设置"),
    ):
        assert f'data-manager-view-link="{view}"' in html
        assert f'data-manager-view="{view}"' in html
        assert label in html

    assert 'src="/api-client.js' in html
    assert 'src="/manager-console.js' in html
    assert 'href="/workspace.css' in html
    assert 'src="/overview.js' not in html


def test_manager_overview_separates_cluster_metrics_and_current_work() -> None:
    html = _source("index.html")

    for marker in (
        'id="manager-machine-total"',
        'id="manager-ready-total"',
        'id="manager-account-total"',
        'id="manager-active-task-total"',
        'id="manager-result-total"',
        'id="manager-exception-total"',
        'id="manager-task-list"',
        'id="manager-machine-list"',
    ):
        assert marker in html


def test_manager_account_directory_is_grouped_by_machine_and_slot() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'data-manager-account-node="all"' in html
    assert 'data-manager-account-node="manager"' in html
    assert 'id="manager-account-node-list"' in html
    assert 'id="manager-local-account-grid"' in html
    assert 'id="manager-remote-account-grid"' in html
    assert 'id="manager-account-filter-status"' in html
    assert 'id="manager-account-filter-persona"' in html
    assert 'id="manager-account-filter-task"' in html
    assert "function renderAccountWorkspace()" in script
    assert 'localStorage.setItem("auto-xhs:manager-account-node"' in script
    assert '["机器 / 槽位", `${account.machine_id} / ${account.account_slot}`]' in script
    assert "account.nickname || account.account_slot" in script
    assert '["数据来源", account.source_label || "员工机回执"]' in script
    assert '["更新时间", formatTime(account.updated_at' in script


def test_remote_account_recovery_actions_keep_machine_and_slot_context() -> None:
    script = _source("manager-console.js")

    assert "function compactAccountCard(account, allowActions = true)" in script
    assert "button.dataset.managerMachine = account.machine_id" in script
    assert "button.dataset.managerAccount = account.account_slot" in script
    assert 'doctor: "doctor"' in script
    assert '"profile-start": "profile/start"' in script
    assert '"bridge-restart": "bridge/restart"' in script
    assert '"identity-check": "identity/check"' in script
    assert "compactAccountCard(account, false)" in script


def test_account_management_and_field_requests_are_manager_owned() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'id="manager-account-management-dialog"' in html
    assert 'id="manager-account-note"' in html
    assert 'id="manager-account-scheduling-enabled"' in html
    assert 'id="manager-account-request-login"' in html
    assert 'id="manager-account-request-identity"' in html
    assert "function openAccountManagement(machineId, accountSlot)" in script
    assert "/management`" in script
    assert "/field-request`" in script
    assert "scheduling_enabled" in script
    assert "request_type: requestType" in script


def test_personas_are_edited_centrally_and_bound_to_machine_account() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'id="manager-persona-form"' in html
    assert 'id="manager-persona-machine"' in html
    assert 'id="manager-persona-account"' in html
    assert 'id="manager-persona-list"' in html
    assert "function renderPersonas()" in script
    assert "manager-persona-machine" in script
    assert "manager-persona-account" in script


def test_operation_tasks_have_filters_detail_and_agent_timeline() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    for marker in (
        'id="manager-operation-form"',
        'id="manager-operation-filter-state"',
        'id="manager-operation-filter-machine"',
        'id="manager-operation-filter-account"',
        'id="manager-operation-filter-date"',
        'id="manager-operation-filter-keyword"',
        'id="manager-operation-detail-dialog"',
        'id="manager-operation-timeline"',
        'id="manager-operation-technical-json"',
    ):
        assert marker in html
    assert "function renderOperationTasks()" in script
    assert "function renderOperationTaskDetail(operationTaskId, openDialog = false)" in script
    assert "progressStageLabels" in script
    assert 'stage === "comment_generated"' in script
    assert "manager-timeline-metric" in script


def test_results_keep_current_incremental_and_audit_views_separate() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'id="manager-result-list"' in html
    assert 'id="manager-incremental-list"' in html
    assert 'id="manager-result-audit-list"' in html
    assert 'id="manager-operation-report-list"' in html
    assert "function renderResults()" in script


def test_manager_console_uses_cached_snapshot_and_serial_refresh() -> None:
    script = _source("manager-console.js")

    assert "const DASHBOARD_REFRESH_INTERVAL_MS = 30_000" in script
    assert "const TASK_REFRESH_INTERVAL_MS = 5_000" in script
    assert "fetchJson(`${managerApi}/dashboard-snapshot`)" in script
    assert "fetchJson(`${api}/accounts`)" in script
    assert "Promise.allSettled" in script
    assert "if (refreshPromise) return refreshPromise" in script
    assert "setInterval(" not in script
    assert 'document.addEventListener("visibilitychange"' in script


def test_manager_console_has_responsive_account_and_task_layouts() -> None:
    stylesheet = _source("workspace.css")

    for selector in (
        ".manager-account-layout",
        ".manager-account-node-list",
        ".manager-account-summary",
        ".manager-operation-layout",
        ".manager-operation-detail-dialog",
        ".manager-operation-detail-columns",
        ".manager-timeline-progress",
        ".manager-timeline-comment",
    ):
        assert selector in stylesheet
    assert "@media (max-width: 1100px)" in stylesheet
    assert "@media (max-width: 820px)" in stylesheet


def test_manager_console_applies_card_and_control_styles_to_new_forms() -> None:
    html = _source("index.html")
    stylesheet = _source("workspace.css")

    assert "20260904-manager-console-3" in html
    assert ".manager-app-shell .subpage-panel" in stylesheet
    assert '.manager-app-shell input:not([type="checkbox"]):not([type="radio"])' in stylesheet
    assert ".manager-app-shell textarea" in stylesheet
    assert 'class="manager-confirm-check"><input id="manager-version-stable"' in html


def test_manager_console_uses_responsive_top_navigation() -> None:
    html = _source("index.html")
    stylesheet = _source("workspace.css")

    assert '<header class="sidebar manager-top-navigation"' in html
    assert ".manager-app-shell > .manager-top-navigation" in stylesheet
    assert "grid-template-columns: auto minmax(0, 1fr) auto" in stylesheet
    assert ".manager-app-shell .manager-top-navigation .nav-list" in stylesheet
    assert "@media (max-width: 720px)" in stylesheet
    assert "min-height: 104px" in stylesheet


def test_system_settings_include_runtime_model_and_diagnostics() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'id="manager-runtime-settings-form"' in html
    assert 'id="manager-reply-model-form"' in html
    assert 'id="manager-reply-model-api-key"' in html
    assert 'id="manager-export-diagnostics"' in html
    assert "`${api}/system/settings`" in script
    assert "`${api}/reply-intelligence/settings`" in script
    assert "`${api}/reply-intelligence/test`" in script
    assert "`${api}/diagnostics/export`" in script


def test_api_client_errors_keep_the_server_payload_for_business_readback() -> None:
    script = _source("api-client.js")

    assert script.count("error.payload = payload;") == 2
    assert script.count("error.httpStatus = response.status;") == 2
    assert script.count('error.code = payload.error?.code || "REQUEST_FAILED";') == 2
    assert "error?.payload && !error.payload.error" in _source("manager-console.js")
