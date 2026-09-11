from __future__ import annotations

import re
from pathlib import Path


def test_webui_classifies_message_receipts_and_keeps_collection_in_worker() -> None:
    root = Path(__file__).parents[1]
    script = (root / "webui" / "manager-console.js").read_text(encoding="utf-8")
    html = (root / "webui" / "index.html").read_text(encoding="utf-8")
    combined = f"{html}\n{script}"

    assert re.search(r"回复\s*·?\s*目标\s*/\s*实际", html)
    assert "function resultActionKind(result)" in script
    assert 'capability.includes("reply")' in script
    assert 'capability.includes("comment")' in script
    assert '["browse", "like", "comment", "reply", "publish"]' in script
    assert 'capability === "send-private-messages"' in script
    assert 'acceptance_action === "private_reply"' in script

    for removed in (
        "private-message-collection-form",
        "intelligent-private-message-list",
        "collect-private-messages",
        "reply-rule-form",
    ):
        assert removed not in combined
    assert 'data-capability="send-private-messages"' not in html
