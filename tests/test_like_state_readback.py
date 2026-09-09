from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from xhs.errors import ActionResultUnknownError, InteractionStateError
from xhs.like_favorite import like_current_feed


def state(liked: bool) -> str:
    return json.dumps({"note-1": {"note": {"interactInfo": {"liked": liked}}}})


@pytest.mark.parametrize("readback", ["", state(False)])
def test_unconfirmed_like_clicks_once(monkeypatch, readback) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    page = Mock()
    page.evaluate.side_effect = [state(False)] + [readback] * 10
    with pytest.raises(ActionResultUnknownError):
        like_current_feed(page, "note-1")
    page.click_element.assert_called_once()


def test_delayed_like_readback_succeeds(monkeypatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    page = Mock()
    page.evaluate.side_effect = [state(False), "", state(False), state(True)]
    result = like_current_feed(page, "note-1")
    assert result.success and result.changed
    page.click_element.assert_called_once()


def test_missing_initial_state_prevents_toggle() -> None:
    page = Mock()
    page.evaluate.return_value = ""
    with pytest.raises(InteractionStateError):
        like_current_feed(page, "note-1")
    page.click_element.assert_not_called()


def test_existing_like_has_no_new_change() -> None:
    page = Mock()
    page.evaluate.return_value = state(True)
    result = like_current_feed(page, "note-1")
    assert result.success and result.changed is False
    page.click_element.assert_not_called()
