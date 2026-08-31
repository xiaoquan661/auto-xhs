from pathlib import Path


def test_extension_enter_dispatch_uses_complete_cdp_sequence() -> None:
    background = (
        Path(__file__).parents[1] / "extension" / "background.js"
    ).read_text(encoding="utf-8")

    assert 'type: key === "Enter" ? "rawKeyDown" : "keyDown"' in background
    assert 'type: "char"' in background
    assert 'text: "\\r"' in background
    assert 'unmodifiedText: "\\r"' in background
    assert 'key === "Enter"' in background


def test_extension_private_message_send_is_one_debugger_transaction() -> None:
    background = (
        Path(__file__).parents[1] / "extension" / "background.js"
    ).read_text(encoding="utf-8")

    assert 'case "send_content_editable"' in background
    assert "async function cmdSendContentEditableViaDebugger" in background
    assert '"Input.insertText", { text }' in background
    assert 'entered !== String(text || "").trim()' in background
    assert "return { entered, submitted: true }" in background
