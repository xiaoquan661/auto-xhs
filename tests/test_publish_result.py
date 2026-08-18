from __future__ import annotations

import pytest

from xhs.errors import AccountRiskControlError
from xhs.publish import click_publish_button


class PublishPage:
    def __init__(self, observations: list[dict]) -> None:
        self.observations = list(observations)
        self.click_point: tuple[float, float] | None = None

    def evaluate(self, expression: str):
        if expression == "location.href":
            return "https://creator.xiaohongshu.com/publish/publish?source=official"
        if "status: 'ready'" in expression:
            return {"status": "ready", "x": 712, "y": 855}
        if "note_manager_title_match" in expression:
            if len(self.observations) > 1:
                return self.observations.pop(0)
            return self.observations[0] if self.observations else {}
        return None

    def mouse_click(self, x: float, y: float) -> None:
        self.click_point = (x, y)


def _observation(**changes) -> dict:
    value = {
        "feedback": None,
        "url": "https://creator.xiaohongshu.com/publish/publish?source=official",
        "url_changed": False,
        "host_present": True,
        "success_marker": False,
        "note_manager_title_match": False,
    }
    value.update(changes)
    return value


def test_click_publish_returns_verified_platform_response(monkeypatch) -> None:
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)
    page = PublishPage(
        [
            _observation(
                feedback={
                    "source": "xhr",
                    "code": 0,
                    "msg": "success",
                    "data": {"note_id": "note-1"},
                }
            )
        ]
    )

    result = click_publish_button(page, expected_title="测试标题")

    assert result["verified"] is True
    assert result["evidence"] == "platform_response"
    assert result["note_id"] == "note-1"
    assert page.click_point == (712.0, 855.0)


def test_click_publish_accepts_new_success_toast(monkeypatch) -> None:
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)
    page = PublishPage(
        [
            _observation(
                feedback={
                    "source": "toast_success",
                    "success": True,
                    "msg": "发布成功",
                }
            )
        ]
    )

    result = click_publish_button(page, expected_title="测试标题")

    assert result["verified"] is True
    assert result["evidence"] == "success_toast"


def test_click_publish_accepts_note_manager_title_readback(monkeypatch) -> None:
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)
    page = PublishPage(
        [
            _observation(
                url="https://creator.xiaohongshu.com/new/note-manager",
                url_changed=True,
                host_present=False,
                note_manager_title_match=True,
            )
        ]
    )

    result = click_publish_button(page, expected_title="测试标题")

    assert result["verified"] is True
    assert result["evidence"] == "note_manager_readback"


def test_click_publish_accepts_changed_success_page(monkeypatch) -> None:
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)
    page = PublishPage(
        [
            _observation(
                url="https://creator.xiaohongshu.com/publish/success",
                url_changed=True,
                host_present=False,
                success_marker=True,
            )
        ]
    )

    result = click_publish_button(page, expected_title="测试标题")

    assert result["verified"] is True
    assert result["evidence"] == "success_page"


def test_click_publish_does_not_trust_static_success_text(monkeypatch) -> None:
    times = iter([0.0, 16.0])
    monkeypatch.setattr("xhs.publish.time.monotonic", lambda: next(times))
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)
    page = PublishPage([_observation(success_marker=True)])

    result = click_publish_button(page, expected_title="测试标题")

    assert result["verified"] is False
    assert result["status"] == "result_unknown"


def test_click_publish_timeout_returns_result_unknown(monkeypatch) -> None:
    times = iter([0.0, 16.0])
    monkeypatch.setattr("xhs.publish.time.monotonic", lambda: next(times))
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)

    result = click_publish_button(PublishPage([_observation()]), expected_title="测试标题")

    assert result["verified"] is False
    assert result["status"] == "result_unknown"
    assert result["evidence"] == "none"


def test_click_publish_surfaces_risk_control(monkeypatch) -> None:
    monkeypatch.setattr("xhs.publish.time.sleep", lambda _seconds: None)
    page = PublishPage(
        [
            _observation(
                feedback={
                    "source": "xhr",
                    "code": -9136,
                    "msg": "禁止发笔记",
                }
            )
        ]
    )

    with pytest.raises(AccountRiskControlError):
        click_publish_button(page, expected_title="测试标题")
