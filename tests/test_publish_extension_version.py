from __future__ import annotations

import json

import pytest
from project_paths import extension_root
from xhs.bridge import BridgePage
from xhs.errors import PublishError
from xhs.publish import click_publish_button


def test_publish_click_requires_loaded_extension_version(monkeypatch) -> None:
    page = BridgePage("ws://localhost:9334", account="2-2", platform="xiaohongshu")
    expected = json.loads((extension_root() / "manifest.json").read_text(encoding="utf-8"))[
        "version"
    ]
    calls: list[str] = []
    monkeypatch.setattr(
        page,
        "get_server_status",
        lambda: {"extension": {"extension_version": "older"}},
    )
    monkeypatch.setattr(page, "_call", lambda method, params: calls.append(method))
    result = page.click_publish_control()
    assert result["error_code"] == "EXTENSION_RELOAD_REQUIRED"
    assert result["expected_version"] == expected
    assert calls == []

    monkeypatch.setattr(
        page,
        "get_server_status",
        lambda: {"extension": {"extension_version": expected}},
    )
    monkeypatch.setattr(page, "_call", lambda method, params: {"dispatched": True})
    assert page.click_publish_control()["dispatched"] is True


def test_publish_version_mismatch_does_not_retry(monkeypatch) -> None:
    class Page:
        def evaluate(self, expression: str):
            if expression == "location.href":
                return "https://creator.xiaohongshu.com/publish/publish"
            if "status: 'ready'" in expression:
                return {"status": "ready", "x": 712, "y": 855}
            return None

    page = Page()
    calls: list[int] = []

    def click() -> dict:
        calls.append(1)
        return {"dispatched": False, "error_code": "EXTENSION_RELOAD_REQUIRED"}

    monkeypatch.setattr(page, "click_publish_control", click, raising=False)
    with pytest.raises(PublishError) as caught:
        click_publish_button(page, expected_title="测试标题")
    assert caught.value.code == "EXTENSION_RELOAD_REQUIRED"
    assert len(calls) == 1
