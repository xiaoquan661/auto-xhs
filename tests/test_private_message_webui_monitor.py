from __future__ import annotations

from pathlib import Path


def test_webui_reports_message_and_reply_modules_without_message_operations() -> None:
    root = Path(__file__).parents[1]
    script = (root / "webui" / "manager-console.js").read_text(encoding="utf-8")
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    combined = f"{html}\n{script}"

    assert "回复 目标/实际" in html
    assert "function resultActionKind(result)" in script
    assert 'capability.includes("reply")' in script
    assert 'capability.includes("comment")' in script
    assert '["browse", "like", "comment", "reply", "publish"]' in script

    for removed in (
        "private-message-collection-form",
        "intelligent-private-message-list",
        "collect-private-messages",
        "send-private-messages",
        "reply-rule-form",
    ):
        assert removed not in combined
