from __future__ import annotations

import copy
import json
from pathlib import Path

from xhs.growth_notifications import collect_growth_notifications, parse_growth_api
from xhs.notification_history import notification_api_complete, read_notification_history
from xhs.notifications import collect_comment_notifications

from tests.test_notifications import NotificationPage

FIXTURES = Path(__file__).parent / "fixtures" / "notifications"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_received_comments_device_response_replays_five_complete_events(monkeypatch) -> None:
    monkeypatch.setattr("xhs.notifications.sleep_random", lambda *_: None)
    result = collect_comment_notifications(NotificationPage(fixture("received-comments.json")))

    assert result["count"] == 5
    assert result["partial"] is False
    assert result["source"] == "notification_api_observation"
    assert {row["classification"] for row in result["comments"]} == {"comment/item"}
    assert result["collection_batches"][0]["source_exhausted"] is True


def test_received_comments_api_confirms_empty_history(monkeypatch) -> None:
    monkeypatch.setattr("xhs.notifications.sleep_random", lambda *_: None)
    captured = fixture("received-comments.json")
    captured["payload"]["data"]["message_list"] = []

    result = collect_comment_notifications(NotificationPage(captured))

    assert result["count"] == 0
    assert result["partial"] is False
    assert result["collection_batches"][0]["coverage"] == "complete"


def test_received_comment_history_advances_through_mention_pages(monkeypatch) -> None:
    monkeypatch.setattr("xhs.notifications.sleep_random", lambda *_: None)
    original = fixture("received-comments.json")["payload"]["data"]["message_list"][0]
    pages = []
    for index, kind in enumerate(["mention/item", "mention/comment", "comment/item"]):
        item = {**original, "id": f"event-{index}", "type": kind}
        cursor = f"page-{index}" if index else ""
        pages.append(
            {
                "url": f"https://example.test/mentions?cursor={cursor}",
                "payload": {
                    "data": {
                        "message_list": [item],
                        "has_more": index < 2,
                        "strCursor": f"page-{index + 1}",
                    }
                },
            }
        )

    class Page(NotificationPage):
        page_index = 0
        history_clicks = 0

        def evaluate(self, expression: str):
            if "window.__AUTO_XHS_MENTIONS__" in expression:
                return json.dumps(pages[self.page_index])
            if "load-more-button" in expression:
                self.page_index += 1
                self.history_clicks += 1
                return True
            return super().evaluate(expression)

    page = Page()
    result = collect_comment_notifications(page)

    assert page.history_clicks == 2
    assert result["count"] == 1
    assert result["comments"][0]["classification"] == "comment/item"
    assert result["partial"] is False


def test_comment_api_replaces_initial_dom_observation(monkeypatch) -> None:
    monkeypatch.setattr("xhs.notifications.sleep_random", lambda *_: None)

    class Page(NotificationPage):
        reads = 0

        def evaluate(self, expression: str):
            if "window.__AUTO_XHS_MENTIONS__" in expression:
                self.reads += 1
                if self.reads == 1:
                    return None
            if "const relationPhrases" in expression:
                return [{"notification_id": "dom:reader:content", "content": "收到评论1"}]
            return super().evaluate(expression)

    result = collect_comment_notifications(Page(fixture("received-comments.json")))

    assert result["count"] == 5
    assert result["partial"] is False
    assert all(
        row["notification_id"].startswith("comment-notification-") for row in result["comments"]
    )


def test_history_waits_for_the_first_async_notification_response(monkeypatch) -> None:
    monkeypatch.setattr("xhs.notification_history.time.sleep", lambda _: None)
    reads = 0

    class Page:
        def wait_dom_stable(self) -> None:
            pass

        def evaluate(self, _expression: str):
            return False

    def read() -> list[dict]:
        nonlocal reads
        reads += 1
        return [{"notification_id": "received-1"}] if reads >= 3 else []

    rows, complete, _ = read_notification_history(
        Page(),
        read,
        api_exhausted=lambda: True if reads >= 3 else None,
    )

    assert reads == 3
    assert complete and len(rows) == 1


def test_received_followers_device_pages_replay_seven_events() -> None:
    captured = fixture("received-followers.json")
    path = "/api/sns/web/v1/you/connections"
    first = copy.deepcopy(captured)
    first[path]["pages"] = first[path]["pages"][:1]

    first_rows, first_complete = parse_growth_api(first, "new_followers", "2026-10-14T08:00:00Z")
    rows, complete = parse_growth_api(captured, "new_followers", "2026-10-14T08:00:00Z")

    assert len(first_rows) == 2 and not first_complete
    assert len(rows) == 7 and complete
    assert {row["event_type"] for row in rows} == {"new_follower"}
    assert notification_api_complete(captured[path]["pages"])


def test_received_followers_collection_loads_observed_history_button(monkeypatch) -> None:
    monkeypatch.setattr("xhs.growth_notifications.time.sleep", lambda _: None)
    captured = fixture("received-followers.json")
    path = "/api/sns/web/v1/you/connections"

    class Page:
        clicks = 0

        def navigate(self, _url: str) -> None:
            pass

        def wait_for_load(self) -> None:
            pass

        def wait_dom_stable(self) -> None:
            pass

        def evaluate(self, expression: str):
            if "const labels =" in expression:
                assert "新增关注" in expression and "赞和收藏" not in expression
                return True
            if expression == "window.__AUTO_XHS_GROWTH__ || {}":
                pages = captured[path]["pages"][:1] if not self.clicks else captured[path]["pages"]
                return {path: {"pages": pages}}
            if "load-more-button" in expression:
                self.clicks += 1
                return True
            if "const rows = [], seen" in expression:
                return []
            return None

    page = Page()
    result = collect_growth_notifications(page, metrics=("new_followers",))

    assert page.clicks == 1
    assert len(result["events"]) == 7
    assert result["coverage"] == "complete"
    assert result["tabs_observed"] == [True]
