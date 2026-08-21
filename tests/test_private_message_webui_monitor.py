from __future__ import annotations

from pathlib import Path


def test_webui_monitors_outbound_batches_and_supports_reviewed_inbox_replies() -> None:
    root = Path(__file__).parents[1]
    script = "\n".join(
        (root / "webui" / name).read_text(encoding="utf-8")
        for name in ("task-catalog.js", "app.js")
    )
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")

    assert '"send-private-messages": { label: "个性化私信" }' in script
    assert 'agentMonitorCapabilities = new Set([...publishMonitorCapabilities, "send-private-messages"])' in script
    assert "!agentMonitorCapabilities.has(item.capability)" in script
    assert 'result.result_type === "private_message_batch"' in script
    assert "appendPrivateMessageResults(content, result)" in script
    assert 'id="private-message-collection-form"' in html
    assert 'id="intelligent-private-message-list"' in html
    assert 'capability: "collect-private-messages"' in script
    assert "function renderIntelligentPrivateMessageEvents" in script
    assert 'draft.action_type === "send-private-messages"' in script
