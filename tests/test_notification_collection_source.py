"""Notification collection remains independent of account profile loading."""
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("platform", ["xiaohongshu", "rednote"])
def test_notifications_collect_without_profile_and_preserve_batches(monkeypatch, platform):
    import business_runner
    from xhs import growth_notifications, metrics_collector

    monkeypatch.setattr(business_runner, "load_account", lambda *a, **kw: Mock(
        platform=platform, name="a", bridge_url="local", account_id="a", bridge_token=""))
    monkeypatch.setattr(business_runner, "BridgePage", Mock())
    profile = Mock(side_effect=RuntimeError("profile unavailable"))
    monkeypatch.setattr(metrics_collector, "collect_operations_metrics", profile)
    growth = {"captured_at": "2026-09-16T08:00:00+00:00", "partial": True,
              "events": [{"platform_event_id": "notification-1"}],
              "collection_batches": [{"metric": "received_likes", "coverage": "partial"}]}
    collect = Mock(return_value=growth)
    monkeypatch.setattr(growth_notifications, "collect_growth_notifications", collect)
    result = business_runner.BusinessRunner._execute_existing_adapter(
        "a", "collect-operations-metrics", {"source": "notification_history"})
    profile.assert_not_called()
    collect.assert_called_once()
    assert result["growth_notifications"] == growth
    assert result["partial"] is True
    assert result["source"] == "notification_history"


def test_rednote_private_collection_uses_read_adapter(monkeypatch):
    import business_runner
    from xhs import private_inbox

    monkeypatch.setattr(business_runner, "load_account", lambda *a, **kw: Mock(
        platform="rednote", name="a", bridge_url="local", account_id="a", bridge_token=""))
    monkeypatch.setattr(business_runner, "BridgePage", Mock())
    collect = Mock(return_value={"messages": [], "partial": False})
    monkeypatch.setattr(private_inbox, "collect_private_messages", collect)
    result = business_runner.BusinessRunner._execute_existing_adapter(
        "a", "collect-private-messages", {"max_conversations": 20})
    collect.assert_called_once()
    assert result["partial"] is False


def test_growth_waits_for_notification_tabs(monkeypatch):
    from xhs import growth_notifications, notification_history

    page = Mock()
    attempts = 0
    def evaluate(script):
        nonlocal attempts
        if "const labels =" in script:
            attempts += 1
            return attempts >= 3
        return {}
    page.evaluate.side_effect = evaluate
    monkeypatch.setattr(growth_notifications.time, "sleep", lambda _: None)
    monkeypatch.setattr(notification_history, "read_notification_history",
                        lambda *a, **kw: ([], True, "历史末尾"))
    result = growth_notifications.collect_growth_notifications(page)
    assert attempts == 4
    assert all(b["coverage"] == "complete" for b in result["collection_batches"])


def test_growth_reads_history_after_async_response(monkeypatch):
    from xhs import growth_notifications, notification_history

    page = Mock()
    polls = 0
    response = {f"/api/sns/web/v1/you/{channel}": {
        "url": f"https://example.test/{channel}?cursor=",
        "payload": {"data": {"message_list": [], "has_more": False}}}
        for channel in ["likes", "connections"]}
    def evaluate(script):
        nonlocal polls
        if "const labels =" in script:
            return True
        if script == "window.__AUTO_XHS_GROWTH__ || {}":
            polls += 1
            return response if polls >= 3 else {}
        return {}
    def history(*args, **kwargs):
        assert polls >= 3
        return [], True, "历史末尾"
    page.evaluate.side_effect = evaluate
    monkeypatch.setattr(growth_notifications.time, "sleep", lambda _: None)
    monkeypatch.setattr(notification_history, "read_notification_history", history)
    result = growth_notifications.collect_growth_notifications(page)
    assert all(b["coverage"] == "complete" for b in result["collection_batches"])
