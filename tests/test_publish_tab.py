from __future__ import annotations

import pytest
from xhs.errors import PublishError
from xhs.publish import _click_publish_tab, _navigate_to_publish_page
from xhs.publish_links import MANAGER_URL


class PublishEntryPage:
    def __init__(self, platform: str = "xiaohongshu") -> None:
        self._platform = platform
        self.events: list[tuple] = []

    def navigate(self, url: str) -> None:
        self.events.append(("navigate", url))

    def wait_for_load(self, *, timeout: int) -> None:
        self.events.append(("wait_for_load", timeout))

    def wait_dom_stable(self) -> None:
        self.events.append(("wait_dom_stable",))


def test_image_publish_opens_creator_menu_before_choosing_type(monkeypatch) -> None:
    page = PublishEntryPage()
    selected: list[str] = []
    monkeypatch.setattr(
        "xhs.publish._click_visible_publish_text",
        lambda _page, label: selected.append(label) or True,
    )
    active = iter([None, "上传图文"])
    monkeypatch.setattr("xhs.publish._active_publish_tab", lambda _page: next(active))
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _: None)

    _navigate_to_publish_page(page)
    _click_publish_tab(page, "上传图文")

    assert page.events == [
        ("navigate", MANAGER_URL),
        ("wait_for_load", 300),
        ("wait_dom_stable",),
    ]
    assert selected == ["发布笔记", "上传图文"]


def test_rednote_uses_its_existing_publish_page(monkeypatch) -> None:
    page = PublishEntryPage("rednote")
    monkeypatch.setattr(
        "xhs.publish._click_visible_publish_text",
        lambda _page, _label: pytest.fail("RedNote 应沿用自身发布页入口"),
    )

    _navigate_to_publish_page(page)

    assert page.events[0] == (
        "navigate",
        "https://creator.xiaohongshu.com/publish/publish?source=official",
    )


def test_active_publish_type_is_not_clicked_again(monkeypatch) -> None:
    selected: list[str] = []
    monkeypatch.setattr("xhs.publish._active_publish_tab", lambda _page: "上传图文")
    monkeypatch.setattr(
        "xhs.publish._click_visible_publish_text",
        lambda _page, label: selected.append(label) or True,
    )

    _click_publish_tab(PublishEntryPage(), "上传图文")

    assert selected == []


def test_publish_type_requires_editor_confirmation(monkeypatch) -> None:
    times = iter([0.0, 0.0, 16.0])
    selected: list[str] = []
    monkeypatch.setattr("xhs.publish.time.monotonic", lambda: next(times))
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _: None)
    monkeypatch.setattr("xhs.publish._active_publish_tab", lambda _page: None)
    monkeypatch.setattr(
        "xhs.publish._click_visible_publish_text",
        lambda _page, label: selected.append(label) or True,
    )

    with pytest.raises(PublishError, match="未能打开“上传图文”编辑页"):
        _click_publish_tab(PublishEntryPage(), "上传图文")

    assert selected == ["上传图文"]
