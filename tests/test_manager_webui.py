from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"


def _source(name: str) -> str:
    return (WEBUI / name).read_text(encoding="utf-8")


def test_manager_console_exposes_six_product_views() -> None:
    html = _source("index.html")

    for view, label in (
        ("employee-units", "全部账号"),
        ("workers", "员工机"),
        ("accounts", "账号管理"),
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
    for view in ("overview", "planning"):
        assert f'data-manager-view="{view}"' not in html
        assert f'data-manager-view-link="{view}"' not in html
    assert 'src="/manager-planning.js' not in html
    assert 'data-manager-view="operations"' not in html
    assert 'data-manager-view-link="operations"' not in html


def test_manager_assets_preserve_shared_plan_and_account_styles() -> None:
    css = _source("control-room.css")
    assert not (WEBUI / "manager-planning.js").exists()
    assert ".daily-plan-summary" not in css
    assert ".daily-plan-inputs" not in css
    assert "#planning" not in css
    assert ".daily-plan-numbers { display: grid;" in css
    assert ".worker-next-account .daily-plan-numbers { padding: 10px 0; }" in css
    assert ".manager-account-tools label {" in css
    assert ".manager-account-tools select, .manager-account-tools textarea {" in css


def test_account_workspace_displays_units_and_attention() -> None:
    html = _source("index.html")
    for marker in (
        'id="employee-unit-content"', 'id="manager-machine-nav"',
        'id="manager-overview-exception-total"', 'id="manager-exception-total"',
    ):
        assert marker in html


def test_manager_account_directory_is_grouped_by_machine_and_slot() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'data-manager-account-node="all"' in html
    assert 'data-manager-account-node="manager"' not in html
    assert 'id="manager-account-node-list"' in html
    assert 'id="manager-local-account-grid"' not in html
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
    assert 'accountTools.open("identity-check", target)' in script
    assert '${base}/identity/check' in _source("manager-account-tools.js")
    assert "card.appendChild(accountPersonaPanel(account))" in script


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


def test_personas_are_displayed_with_their_worker_account() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    assert 'id="manager-persona-form"' not in html
    assert 'id="manager-persona-machine"' not in html
    assert "function accountPersonaPanel(account)" in script
    assert 'id="manager-persona-editor-form"' in html
    assert "function openPersonaEditor(account)" in script
    assert 'mutateJson(`${managerApi}/personas`, "POST", body)' in script
    assert "card.appendChild(accountPersonaPanel(account))" in script
    assert "function submitPersona(" not in script
    assert "item.account_slot === account.account_slot" in script
    assert "item.machine_id === account.machine_id" in script


def test_operation_tasks_keep_detail_and_agent_timeline() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    for marker in (
        'id="manager-goal-detail-dialog"',
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
    assert 'id="manager-goal-form"' not in html
    assert 'id="manager-operation-form"' not in html
    assert 'id="manager-task-list"' not in html
    assert 'id="manager-active-task-total"' not in html
    assert "function submitGoal(" not in script
    assert "function submitOperationTask(" not in script


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
    assert "fetchJson(`${api}/accounts`)" not in script
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

    assert "20260907-worker-progress-1" in html
    assert ".manager-app-shell .subpage-panel" in stylesheet
    assert '.manager-app-shell input:not([type="checkbox"]):not([type="radio"])' in stylesheet
    assert ".manager-app-shell textarea" in stylesheet


def test_manager_console_uses_control_room_navigation() -> None:
    html = _source("index.html")
    stylesheet = _source("control-room.css")

    assert 'href="/control-room.css' in html
    assert '<header class="room-masthead"' in html
    assert 'class="room-dashboard"' in html
    assert html.count('id="manager-machine-nav"') == 1
    assert "grid-template-columns: 248px minmax(0, 1fr)" in stylesheet
    assert "@media (max-width: 900px)" in stylesheet
    assert "@media (max-width: 600px)" in stylesheet


def test_system_settings_display_agent_managed_configuration() -> None:
    html = _source("index.html")
    script = _source("manager-console.js")

    settings = html.split('id="settings"', 1)[1].split("</main>", 1)[0]
    assert 'id="manager-polling-form"' in settings
    assert 'id="manager-polling-save"' in settings
    assert 'src="/manager-polling.js' in html
    assert 'id="manager-setting-concurrency"' in settings
    assert 'id="manager-system-pause-state"' in settings
    assert 'id="manager-settings-machines"' in settings
    assert 'id="manager-transfer-list"' in settings
    assert 'id="manager-version-list"' in settings
    assert "由经理 Agent 登记与维护" in settings
    assert "reply-intelligence" not in script
    assert "submitRuntimeSettings" not in script
    assert "submitMachine" not in script
    assert "submitTransfer" not in script
    assert "submitVersionPolicy" not in script
    assert 'fetchJson(`${api}/system/status`)' in script


def test_api_client_errors_keep_the_server_payload_for_business_readback() -> None:
    script = _source("api-client.js")

    assert script.count("error.payload = payload;") == 2
    assert script.count("error.httpStatus = response.status;") == 2
    assert script.count('error.code = payload.error?.code || "REQUEST_FAILED";') == 2
    assert "error?.payload && !error.payload.error" in _source("manager-console.js")
