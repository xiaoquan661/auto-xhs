from __future__ import annotations

from pathlib import Path


def test_webui_monitors_publish_tasks_without_action_controls() -> None:
    root = Path(__file__).parents[1]
    script = (root / "webui" / "app.js").read_text(encoding="utf-8")
    page = (root / "webui" / "index.html").read_text(encoding="utf-8")

    assert '"fill-publish": { label: "图文发布" }' in script
    assert "appendPublishTaskPreview(card, item)" in script
    assert "!publishMonitorCapabilities.has(item.capability)" in script
    assert "WebUI 会只读监测由 Agent" in page
