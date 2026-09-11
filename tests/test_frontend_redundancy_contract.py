from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("directory", ["webui", "webui/worker"])
def test_console_entrypoint_assets_exist(directory: str) -> None:
    static_root = ROOT / directory
    html = (static_root / "index.html").read_text(encoding="utf-8")
    assets = re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)="([^"]+)"', html)

    assert assets
    for asset in assets:
        url = urlsplit(asset)
        if not url.scheme and not url.netloc:
            assert (static_root / url.path.lstrip("/")).is_file(), asset


def test_content_script_forwards_diagnostic_and_netlog_events() -> None:
    content = (ROOT / "extension" / "content.js").read_text(encoding="utf-8")

    assert content.count('window.addEventListener("message"') == 2
    assert content.count("chrome.runtime.sendMessage") == 2
    assert 'source !== "xhs-interceptor"' in content
    assert 'type: "XHS_BLOCK_EVENT"' in content
    assert 'source !== "xhs-netlog-intercept"' in content
    assert 'type: "NETLOG_INTERCEPTOR_ENTRY"' in content
    assert 'source: "xhs-content-ready"' in content
    assert "handleDomCommand" not in content


def test_file_upload_uses_single_debugger_route() -> None:
    background = (ROOT / "extension" / "background.js").read_text(encoding="utf-8")

    assert background.count('case "set_file_input"') == 1
    assert "return await cmdSetFileInputViaDebugger(params)" in background
    assert '"DOM.setFileInputFiles"' in background
    assert "atob(f.data)" not in background


def test_workspace_initialization_has_single_render_owner() -> None:
    workspace = (ROOT / "webui" / "workspace.js").read_text(encoding="utf-8")
    app = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")

    assert workspace.count("render(window.location.hash, { focus: false })") == 1
    assert "setupNavigation" not in app
    assert "AutoXhsWorkspace?.render" not in app
