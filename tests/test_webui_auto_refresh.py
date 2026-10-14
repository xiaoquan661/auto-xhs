from __future__ import annotations

from pathlib import Path

from project_paths import webui_asset_path

ROOT = Path(__file__).resolve().parents[1]


def _webui_sources() -> tuple[str, str]:
    html = (webui_asset_path("index.html")).read_text(encoding="utf-8")
    script = (webui_asset_path("manager-console.js")).read_text(encoding="utf-8")
    return html, script


def test_webui_exposes_cached_snapshot_time_and_manual_refresh() -> None:
    html, _script = _webui_sources()

    assert 'id="manager-last-read"' in html
    assert 'id="manager-account-refresh"' in html
    assert 'id="manager-cluster-message" role="status"' in html


def test_webui_serializes_dashboard_refreshes_and_accelerates_task_detail() -> None:
    _html, script = _webui_sources()

    assert "const DASHBOARD_REFRESH_INTERVAL_MS = 30_000" in script
    assert "const TASK_REFRESH_INTERVAL_MS = 5_000" in script
    assert "let refreshPromise = null" in script
    assert "if (refreshPromise) return refreshPromise" in script
    assert "refreshPromise = performLoad().finally" in script
    assert "window.setTimeout(() => load(), delay)" in script
    assert "setInterval(" not in script


def test_webui_pauses_polling_while_hidden_and_refreshes_on_return() -> None:
    _html, script = _webui_sources()

    assert 'document.addEventListener("visibilitychange"' in script
    assert "if (document.hidden)" in script
    assert "window.clearTimeout(refreshTimer)" in script
    assert "return;\n      }\n      load();" in script
    assert "load();" in script
