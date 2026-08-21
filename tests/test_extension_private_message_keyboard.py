from pathlib import Path


def test_extension_enter_dispatch_includes_text_fields() -> None:
    background = (
        Path(__file__).parents[1] / "extension" / "background.js"
    ).read_text(encoding="utf-8")

    assert 'text: "\\r", unmodifiedText: "\\r"' in background
    assert 'key === "Enter"' in background
