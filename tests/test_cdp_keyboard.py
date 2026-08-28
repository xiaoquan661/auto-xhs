from __future__ import annotations

from scripts.xhs.cdp import Page


def test_enter_uses_raw_keydown_char_and_keyup(monkeypatch) -> None:
    page = object.__new__(Page)
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(
        page,
        "_send_session",
        lambda method, params: calls.append((method, params)) or {},
    )

    page.press_key("Enter")

    assert [params["type"] for _, params in calls] == ["rawKeyDown", "char", "keyUp"]
    assert calls[1][1]["text"] == "\r"
    assert calls[1][1]["unmodifiedText"] == "\r"


def test_contenteditable_send_inserts_and_submits_atomically(monkeypatch) -> None:
    page = object.__new__(Page)
    calls: list[tuple[str, dict]] = []
    evaluations = iter([True, "准备回复"])

    monkeypatch.setattr(
        page,
        "evaluate",
        lambda _expression: next(evaluations),
    )
    monkeypatch.setattr(
        page,
        "_send_session",
        lambda method, params: calls.append((method, params)) or {},
    )
    monkeypatch.setattr("scripts.xhs.cdp.time.sleep", lambda _seconds: None)

    result = page.send_content_editable(".editor", "准备回复")

    assert [method for method, _ in calls].count("Input.insertText") == 1
    assert calls[4] == ("Input.insertText", {"text": "准备回复"})
    assert [params["type"] for method, params in calls[5:] if method == "Input.dispatchKeyEvent"] == [
        "rawKeyDown",
        "char",
        "keyUp",
    ]
    assert result == {"entered": "准备回复", "submitted": True}
