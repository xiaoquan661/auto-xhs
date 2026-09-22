from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKER_WEBUI = ROOT / "webui" / "worker"


def _source(name: str) -> str:
    return (WORKER_WEBUI / name).read_text(encoding="utf-8")


def test_worker_console_has_only_local_execution_views() -> None:
    html = _source("index.html")

    for view, label in (
        ("overview", "执行概览"),
        ("current", "当前任务"),
        ("history", "执行记录"),
        ("outbox", "回传队列"),
        ("local", "本机状态"),
    ):
        assert f'data-worker-view-link="{view}"' in html
        assert f'data-worker-view="{view}"' in html
        assert label in html
    for manager_only in ("运营目标", "其他机器", "功能包统一管理"):
        assert manager_only not in html


def test_worker_history_and_outbox_expose_required_read_only_details() -> None:
    html = _source("index.html")
    script = _source("worker.js")

    for marker in (
        'id="worker-filter-account"',
        'id="worker-filter-state"',
        'id="worker-filter-capability"',
        'id="worker-filter-date"',
        'id="worker-outbox-facts"',
        'id="worker-outbox-list"',
    ):
        assert marker in html
    assert "function renderHistory()" in script
    assert "function renderOutbox(exchange, records, summaries = [])" in script
    assert "const grouped = summaries.length ? summaries : records" in script
    assert "exchange.pending_tasks ?? exchange.pending_outbox" in script
    assert "last_written_at" in script
    assert "last_collected_at" in script
    assert "重复执行" not in script


def test_worker_actions_are_local_session_protected_controls() -> None:
    html = _source("index.html")
    script = _source("worker.js")

    assert 'data-worker-action="pause"' in html
    assert 'data-worker-action="resume"' in html
    assert 'data-worker-action="restart"' in html
    assert '"/api/v1/worker/pause"' in script
    assert '"/api/v1/worker/resume"' in script
    assert '"/api/v1/worker/executor/restart"' in script
    assert '"X-Auto-XHS-Session": token' in script
    assert "encodeURIComponent(account)" in script
    assert "window.confirm" in script


def test_worker_console_handles_manager_field_requests_locally() -> None:
    html = _source("index.html")
    script = _source("worker.js")

    assert 'id="worker-field-request-panel"' in html
    assert 'id="worker-field-request-list"' in html
    assert "function fieldRequestCard(fieldRequest)" in script
    assert 'dataset.workerFieldAction = "start"' in script
    assert 'dataset.workerFieldAction = "complete"' in script
    assert "/api/v1/worker/account-requests/" in script
    assert "management.account_note" in script
    assert "management.scheduling_enabled" in script


def test_worker_refresh_is_serial_and_pauses_in_background() -> None:
    script = _source("worker.js")

    assert "const REFRESH_INTERVAL_MS = 5_000" in script
    assert "if (loadingPromise) return loadingPromise" in script
    assert "window.setTimeout(load, REFRESH_INTERVAL_MS)" in script
    assert "setInterval(" not in script
    assert 'document.addEventListener("visibilitychange"' in script
    assert "if (document.hidden)" in script


def test_worker_timeline_renders_stage_metrics_and_comment_text() -> None:
    script = _source("worker.js")
    stylesheet = _source("worker.css")

    assert "stageLabels" in script
    assert 'stage === "comment_generated"' in script
    assert "timeline-progress" in script
    assert ".timeline-progress" in stylesheet
    assert ".timeline-comment" in stylesheet


def test_worker_console_has_desktop_and_mobile_layouts() -> None:
    stylesheet = _source("worker.css")

    assert ".worker-sidebar" in stylesheet
    assert ".worker-view" in stylesheet
    assert ".filter-grid" in stylesheet
    assert ".record-grid" in stylesheet
    assert "@media (max-width: 820px)" in stylesheet
    assert "@media (max-width: 560px)" in stylesheet
