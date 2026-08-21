from __future__ import annotations

from pathlib import Path


def test_webui_only_monitors_private_message_tasks_without_action_controls() -> None:
    root = Path(__file__).parents[1]
    script = "\n".join(
        (root / "webui" / name).read_text(encoding="utf-8")
        for name in ("task-catalog.js", "app.js")
    )

    assert '"send-private-messages": { label: "个性化私信" }' in script
    assert 'agentMonitorCapabilities = new Set([...publishMonitorCapabilities, "send-private-messages"])' in script
    assert "!agentMonitorCapabilities.has(item.capability)" in script
    assert 'result.result_type === "private_message_batch"' in script
    assert "appendPrivateMessageResults(content, result)" in script
