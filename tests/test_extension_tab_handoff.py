from __future__ import annotations

from project_paths import extension_root


def test_extension_prefers_active_xhs_tab_after_profile_opens_chat() -> None:
    background = (extension_root() / "background.js").read_text(encoding="utf-8")

    assert "const activeTabs = await chrome.tabs.query" in background
    assert "active: true" in background
    assert "currentWindow: true" in background
    assert "if (activeTabs.length > 0) return activeTabs[0]" in background
