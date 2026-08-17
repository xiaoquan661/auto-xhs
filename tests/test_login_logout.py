from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.xhs.errors import CDPError
from scripts.xhs.login import _click_marked_logout_target, logout
from scripts.xhs.selectors import LOGIN_CONTAINER, LOGIN_STATUS


def test_workspace_extension_supports_verified_session_logout() -> None:
    extension_dir = Path(__file__).resolve().parents[1] / "extension"
    manifest = json.loads((extension_dir / "manifest.json").read_text(encoding="utf-8"))
    background = (extension_dir / "background.js").read_text(encoding="utf-8")

    assert manifest["version"] == "1.2.1"
    assert "cookies" in manifest["permissions"]
    assert 'case "delete_auth_cookies"' in background
    assert 'new Set(["web_session", "id_token"])' in background


class LogoutPage:
    def __init__(
        self,
        *,
        logged_in: bool = True,
        confirm_required: bool = False,
        more_available: bool = True,
        logout_effect: bool = True,
    ) -> None:
        self.logged_in = logged_in
        self.confirm_required = confirm_required
        self.more_available = more_available
        self.logout_effect = logout_effect
        self.menu_open = False
        self.confirm_open = False
        self.clicks: list[str] = []
        self.navigations: list[str] = []

    def navigate(self, url: str) -> None:
        self.navigations.append(url)

    def wait_for_load(self) -> None:
        pass

    def wait_dom_stable(self) -> None:
        pass

    def has_element(self, selector: str) -> bool:
        if selector == LOGIN_STATUS:
            return self.logged_in
        if selector == LOGIN_CONTAINER:
            return not self.logged_in
        return False

    def evaluate(self, expression: str):
        if "const loginContainerSelector" in expression:
            return not self.logged_in
        if 'setAttribute(attribute, "more")' in expression:
            return self.logged_in and self.more_available
        if 'setAttribute(attribute, "menu")' in expression:
            return self.menu_open
        if 'setAttribute(attribute, "confirm")' in expression:
            return self.confirm_open
        if 'a[href*="/user/profile/"]' in expression:
            return "/user/profile/user-old" if self.logged_in else ""
        return ""

    def click_element_by_text(self, selector: str, _text: str) -> None:
        if '="more"' in selector:
            self.menu_open = True
            self.clicks.append("more")
            return
        if '="menu"' in selector:
            self.clicks.append("menu")
            if self.confirm_required:
                self.confirm_open = True
            elif self.logout_effect:
                self.logged_in = False
            return
        if '="confirm"' in selector:
            self.clicks.append("confirm")
            if self.logout_effect:
                self.logged_in = False


def test_logout_uses_visible_targets_and_verifies_logged_out(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)
    page = LogoutPage()

    result = logout(page, target_timeout=0.05, verification_timeout=0.05)

    assert result is True
    assert page.logged_in is False
    assert page.clicks == ["more", "menu"]
    assert page.navigations[1] == "https://www.xiaohongshu.com/user/profile/user-old"


def test_logout_handles_confirmation_dialog(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)
    page = LogoutPage(confirm_required=True)

    result = logout(page, target_timeout=0.05, verification_timeout=0.05)

    assert result is True
    assert page.clicks == ["more", "menu", "confirm"]


def test_logout_returns_false_when_already_logged_out(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)
    page = LogoutPage(logged_in=False)

    assert logout(page, target_timeout=0.05, verification_timeout=0.05) is False
    assert page.clicks == []


def test_logout_reports_missing_more_button(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)
    page = LogoutPage(more_available=False)

    with pytest.raises(RuntimeError, match="更多"):
        logout(page, target_timeout=0.01, verification_timeout=0.01)


def test_logout_rejects_unverified_result(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)
    page = LogoutPage(logout_effect=False)

    with pytest.raises(RuntimeError, match="未检测到明确的未登录界面"):
        logout(page, target_timeout=0.05, verification_timeout=0.01)


def test_logout_expires_session_when_current_web_layout_has_no_account_menu(
    monkeypatch,
) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)

    class NoAccountMenuPage(LogoutPage):
        def evaluate(self, expression: str):
            if 'setAttribute(attribute, "menu")' in expression:
                return False
            if "const hadWebSession" in expression:
                self.logged_in = False
                return {"hadWebSession": True, "hasWebSession": False}
            return super().evaluate(expression)

    page = NoAccountMenuPage()

    assert logout(page, target_timeout=0.01, verification_timeout=0.05) is True
    assert page.logged_in is False
    assert page.clicks == []


def test_logout_uses_extension_for_http_only_session_cookie(monkeypatch) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)

    class HttpOnlySessionPage(LogoutPage):
        def evaluate(self, expression: str):
            if 'setAttribute(attribute, "menu")' in expression:
                return False
            if "const hadWebSession" in expression:
                return {"hadWebSession": False, "hasWebSession": False}
            return super().evaluate(expression)

        def delete_auth_cookies(self):
            self.logged_in = False
            return {"removed": ["web_session", "id_token"], "remaining": []}

    page = HttpOnlySessionPage(more_available=False)

    assert logout(page, target_timeout=0.01, verification_timeout=0.05) is True
    assert page.logged_in is False
    assert page.clicks == []


def test_logout_does_not_treat_temporarily_missing_account_markers_as_success(
    monkeypatch,
) -> None:
    monkeypatch.setattr("scripts.xhs.login.sleep_random", lambda *_args: None)

    class TransientMarkerPage(LogoutPage):
        def __init__(self) -> None:
            super().__init__(logout_effect=False)
            self.markers_visible = True

        def has_element(self, selector: str) -> bool:
            if selector == LOGIN_STATUS:
                return self.logged_in and self.markers_visible
            if selector == LOGIN_CONTAINER:
                return False
            return False

        def evaluate(self, expression: str):
            if "const loginContainerSelector" in expression:
                return False
            if 'a[href*="/user/profile/"]' in expression:
                return (
                    "/user/profile/user-old"
                    if self.logged_in and self.markers_visible
                    else ""
                )
            return super().evaluate(expression)

        def click_element_by_text(self, selector: str, text: str) -> None:
            super().click_element_by_text(selector, text)
            if '="menu"' in selector:
                self.markers_visible = False

    page = TransientMarkerPage()

    with pytest.raises(RuntimeError, match="未检测到明确的未登录界面"):
        logout(page, target_timeout=0.05, verification_timeout=0.01)

    assert page.logged_in is True


def test_marked_logout_target_falls_back_to_page_click_when_bridge_click_fails() -> None:
    class FallbackPage(LogoutPage):
        def click_element_by_text(self, _selector: str, _text: str) -> None:
            raise CDPError("Bridge 点击失败")

        def evaluate(self, expression: str):
            if "document.querySelector(selector)" in expression:
                return True
            return super().evaluate(expression)

    _click_marked_logout_target(FallbackPage(), "more")


def test_marked_logout_target_keeps_original_error_when_fallback_cannot_click() -> None:
    class FailedFallbackPage(LogoutPage):
        def click_element_by_text(self, _selector: str, _text: str) -> None:
            raise CDPError("Bridge 点击失败")

        def evaluate(self, expression: str):
            if "document.querySelector(selector)" in expression:
                return False
            return super().evaluate(expression)

    with pytest.raises(CDPError, match="Bridge 点击失败"):
        _click_marked_logout_target(FailedFallbackPage(), "more")
