from __future__ import annotations

import pytest
from xhs.errors import PublishError
from xhs.publish import _click_publish_tab


class PublishTabPage:
    def __init__(self, tab_result: str, active_title: str | None = None) -> None:
        self.tab_result = tab_result
        self.active_title = active_title
        self.scripts: list[str] = []

    def evaluate(self, expression: str):
        self.scripts.append(expression)
        if "const name =" in expression:
            return self.tab_result
        if "classList.contains('active')" in expression:
            return self.active_title
        return "{}"


def test_click_publish_tab_returns_after_active_tab_is_verified() -> None:
    page = PublishTabPage("clicked", "上传图文")

    _click_publish_tab(page, "上传图文")

    assert len(page.scripts) == 2
    assert "data-hp-bound" in page.scripts[0]


def test_click_publish_tab_raises_when_tab_is_missing(monkeypatch) -> None:
    times = iter([0.0, 0.0, 16.0])
    monkeypatch.setattr("xhs.publish.time.monotonic", lambda: next(times))
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)

    with pytest.raises(PublishError, match="没有找到发布 TAB"):
        _click_publish_tab(PublishTabPage("not_found"), "上传图文")
