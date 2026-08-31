from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEBUI = ROOT / "webui"


def _source(name: str) -> str:
    return (WEBUI / name).read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    assert start in source, f"missing start marker: {start}"
    assert end in source, f"missing end marker: {end}"
    return source.split(start, 1)[1].split(end, 1)[0]


def test_manager_console_entries_are_exposed_in_the_webui() -> None:
    html = _source("index.html")

    expected_entries = {
        'id="manager-cluster-title"': "执行机集群",
        'id="manager-task-title"': "远程任务调度",
        'id="manager-collect-results"': "立即回收所选执行机",
        'id="manager-transfer-form"': "功能包传输",
        'id="manager-version-form"': "设置功能版本策略",
    }
    for marker, label in expected_entries.items():
        assert marker in html, f"WebUI 缺少{label}入口"

    api_client = html.index('src="/api-client.js')
    manager_console = html.index('src="/manager-console.js')
    application = html.index('src="/app.js')
    assert api_client < manager_console < application


def test_automatic_manager_load_reads_cached_collections_only() -> None:
    script = _source("manager-console.js")
    load_body = _between(script, "async function load() {", "function businessPayload(error)")

    expected_reads = (
        "fetchJson(`${managerApi}/machines`)",
        "fetchJson(`${managerApi}/tasks`)",
        "fetchJson(`${managerApi}/transfers`)",
        "fetchJson(`${managerApi}/results`)",
    )
    for request in expected_reads:
        assert request in load_body
    assert load_body.count("fetchJson(") == len(expected_reads)
    assert "/collect" not in load_body
    assert "/versions" not in load_body
    assert "mutateJson(" not in load_body


def test_ssh_checks_result_collection_and_version_reads_require_explicit_events() -> None:
    script = _source("manager-console.js")
    machine_action = _between(
        script,
        "async function handleMachineAction(button) {",
        "async function handleTaskAction(button) {",
    )
    collection = _between(
        script,
        "async function collectSelectedMachine(event) {",
        "async function submitTransfer(event) {",
    )
    version_read = _between(
        script,
        "async function loadVersions(event) {",
        "function bindEvents() {",
    )
    bindings = _between(script, "function bindEvents() {", "function initialize() {")

    assert 'action === "collect"' in machine_action
    assert "window.confirm(" in machine_action
    assert "/collect`" in collection
    assert "window.confirm(" in collection
    assert "/versions${suffix}`" in version_read
    assert (
        '$("#manager-collect-results")?.addEventListener("click", collectSelectedMachine)'
        in bindings
    )
    assert '$("#manager-load-versions")?.addEventListener("click", loadVersions)' in bindings
    assert 'document.addEventListener("click"' in bindings
    assert "handleMachineAction(button)" in bindings


def test_uncertain_remote_tasks_offer_reconciliation_without_redispatch() -> None:
    script = _source("manager-console.js")
    reconciliation_states = _between(
        script,
        "const reconciliationStates = new Set([",
        "]);\n  const stateLabels",
    )
    task_actions = _between(script, "function taskActions(task) {", "function renderTasks() {")
    reconcile_branch = _between(
        task_actions,
        "if (taskNeedsReconciliation(task)) {",
        'if (dispatchState === "PENDING_DISPATCH")',
    )

    assert '"RECONCILIATION_REQUIRED"' in reconciliation_states
    assert '"RESULT_UNKNOWN"' in reconciliation_states
    assert 'actionButton("读取并对账", "task-reconcile"' in reconcile_branch
    assert "return actions;" in reconcile_branch
    assert "task-pause" not in reconcile_branch
    assert "task-resume" not in reconcile_branch
    assert "task-dispatch" not in reconcile_branch
    assert 'actionButton("继续派发", "task-dispatch"' in task_actions
    assert "mutateJson" not in task_actions


def test_manager_mutations_share_the_session_aware_api_client() -> None:
    script = _source("manager-console.js")
    mutation_runner = _between(
        script,
        "async function runMutation(button, path, body, messageSelector, pendingMessage) {",
        "async function handleMachineAction(button) {",
    )

    assert 'payload = await mutateJson(path, "POST", body)' in mutation_runner
    assert "fetch(" not in script
    assert script.count("mutateJson(") == 1

    mutation_paths = (
        "`${managerApi}/machines`",
        "`${managerApi}/tasks`",
        "`${managerApi}/transfers`",
        "`${managerApi}/version-policies`",
    )
    for path in mutation_paths:
        assert path in script
    assert script.count("await runMutation(") >= 7


def test_api_client_errors_keep_the_server_payload_for_business_readback() -> None:
    script = _source("api-client.js")

    assert script.count("error.payload = payload;") == 2
    assert script.count("error.httpStatus = response.status;") == 2
    assert script.count('error.code = payload.error?.code || "REQUEST_FAILED";') == 2
    assert "error?.payload && !error.payload.error" in _source("manager-console.js")


def test_manager_uncertain_operations_keep_ids_and_pending_buttons_locked() -> None:
    script = _source("manager-console.js")
    mutation_runner = _between(
        script,
        "async function runMutation(button, path, body, messageSelector, pendingMessage) {",
        "async function handleMachineAction(button) {",
    )
    transfer_submit = _between(
        script,
        "async function submitTransfer(event) {",
        "async function submitVersionPolicy(event) {",
    )

    assert "pendingActions.add(pendingKey)" in mutation_runner
    assert "pendingActions.delete(pendingKey)" in mutation_runner
    assert 'if (payload.success === true) $("#manager-task-id")' in script
    assert 'if (payload.success === true) $("#manager-transfer-id")' in transfer_submit
    assert '["PREPARING", "UPLOADING", "RESULT_UNKNOWN"]' in transfer_submit
    assert "当前页面不会用新编号重复传输" in transfer_submit


def test_manager_result_summary_and_version_stability_follow_backend_contract() -> None:
    script = _source("manager-console.js")
    result_renderer = _between(script, "function renderResults() {", "function renderVersions() {")
    version_control = _between(
        script,
        "function updateVersionStableControl() {",
        "initialize();",
    )

    assert "state.resultSummary.manual_action_count" in result_renderer
    assert "state.resultSummary.data_gaps" in result_renderer
    assert 'stable.disabled = status !== "FORMAL"' in version_control
    assert 'stable.checked = status === "FORMAL"' in version_control
    assert 'set_as_stable: status === "FORMAL"' in script


def test_business_results_render_feed_details_without_exposing_tokens() -> None:
    script = _source("manager-console.js")
    renderer = _between(
        script,
        "function renderBusinessResult(result) {",
        "function renderResults() {",
    )

    assert "business_result_status" not in renderer
    assert 'resultDataStatus(result, "business_result")' in renderer
    assert "business.feeds" in renderer
    assert "feed.displayTitle" in renderer
    assert "feed.user?.nickname" in renderer
    assert "feed.interactInfo" in renderer
    assert "xsecToken" not in renderer
    assert "innerHTML" not in renderer
    assert 'element("summary", "", `业务数据 · ${count} 条`)' in renderer
