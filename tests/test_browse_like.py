from __future__ import annotations

from unittest.mock import patch

from xhs.browse_like import browse_feed_cycle
from xhs.types import Feed, InteractInfo, NoteCard, User
from xhs.urls import HOME_URL


def test_rednote_discovery_cards_are_browse_candidates() -> None:
    from xhs.browse_like import _browse_candidates, _card_selector, _open_from_feed

    class RedNotePage(FakePage):
        def has_element(self, selector: str) -> bool:
            if "/discovery/item/feed-1" in selector:
                return True
            return super().has_element(selector) if not selector.startswith("a[") else False

        def click_element(self, selector: str) -> None:
            assert "/discovery/item/feed-1" in selector
            self.clicked.append(selector)
            self.current_feed = "feed-1"
            self.url = "https://www.rednote.com/discovery/item/feed-1"

    page = RedNotePage()
    feed = _feed(1)
    with (
        patch("xhs.browse_like.extract_current_feeds", return_value=[feed]),
        patch("xhs.browse_like.time.sleep"),
    ):
        assert _browse_candidates(page, set()) == [feed]
        assert _browse_candidates(page, {"feed-1"}) == []
        _open_from_feed(page, feed)
    assert page.clicked == [_card_selector(feed.id)]
    assert page.navigate_calls == []


def test_open_detail_uses_note_url_when_like_control_has_changed() -> None:
    from xhs.browse_like import _open_from_feed

    class ChangedLikePage(FakePage):
        def has_element(self, selector: str) -> bool:
            if selector == ".interact-container .left .like-lottie":
                return False
            return super().has_element(selector)

    page = ChangedLikePage()
    with patch("xhs.browse_like.time.sleep"):
        _open_from_feed(page, _feed(1))
    assert page.current_feed == "feed-1"


def _feed(index: int) -> Feed:
    return Feed(
        id=f"feed-{index}",
        xsec_token=f"token-{index}",
        model_type="note",
        index=index,
        note_card=NoteCard(
            type="video",
            display_title=f"video-{index}",
            user=User(nickname="tester"),
            interact_info=InteractInfo(liked=False),
        ),
    )


class FakePage:
    def __init__(self) -> None:
        self.current_feed = ""
        self.url = HOME_URL
        self.navigate_calls: list[str] = []
        self.clicked: list[str] = []
        self.scrolls: list[tuple[int, int]] = []

    def navigate(self, url: str) -> None:
        self.navigate_calls.append(url)
        self.url = url

    def wait_for_load(self) -> None:
        pass

    def wait_dom_stable(self) -> None:
        pass

    def evaluate(self, expression: str):
        if expression == "window.location.href":
            return self.url
        if expression == "window.innerHeight || 768":
            return 800
        if expression == "window.history.back()":
            self.current_feed = ""
            self.url = HOME_URL
        return None

    def has_element(self, selector: str) -> bool:
        if selector.startswith('a[href*="/explore/'):
            return True
        if selector == ".interact-container .left .like-lottie":
            return bool(self.current_feed)
        if selector == ".close-circle":
            return bool(self.current_feed)
        return False

    def scroll_element_into_view(self, selector: str) -> None:
        pass

    def click_element(self, selector: str) -> None:
        self.clicked.append(selector)
        if selector.startswith('a[href*="/explore/'):
            self.current_feed = selector.split("/explore/", 1)[1].split('"', 1)[0]
            self.url = f"{HOME_URL}/explore/{self.current_feed}"
        elif selector == ".close-circle":
            self.current_feed = ""
            self.url = HOME_URL

    def press_key(self, key: str) -> None:
        pass

    def simulate_reading_mouse(self, duration_ms: int) -> None:
        pass

    def scroll_by(self, x: int, y: int) -> None:
        self.scrolls.append((x, y))


def test_browse_feed_cycle_scrolls_opens_and_stops_at_count() -> None:
    page = FakePage()
    feeds = [_feed(index) for index in range(5)]

    with (
        patch("xhs.browse_like.extract_current_feeds", return_value=feeds),
        patch("xhs.browse_like.random.choice", side_effect=lambda items: items[0]),
        patch("xhs.browse_like._simulate_reading", return_value=12.0),
        patch("xhs.browse_like.time.sleep"),
    ):
        result = browse_feed_cycle(page, duration_seconds=300, count=3)

    assert page.navigate_calls == [HOME_URL]
    assert len([item for item in page.clicked if "/explore/" in item]) == 3
    assert page.clicked.count(".close-circle") == 3
    assert len(page.scrolls) == 2
    assert result["count"] == 3
    assert result["stop_reason"] == "count_reached"
    assert result["refreshed_between_items"] is False
    assert all(item["read_seconds"] == 12.0 for item in result["items"])


def test_browse_feed_cycle_uses_per_item_read_range_instead_of_dividing_total_time() -> None:
    page = FakePage()
    feeds = [_feed(index) for index in range(3)]

    with (
        patch("xhs.browse_like.extract_current_feeds", return_value=feeds),
        patch("xhs.browse_like.random.choice", side_effect=lambda items: items[0]),
        patch("xhs.browse_like.random.uniform", return_value=11.0),
        patch("xhs.browse_like._simulate_reading", return_value=11.0) as reading,
        patch("xhs.browse_like.time.sleep"),
    ):
        browse_feed_cycle(
            page, duration_seconds=480, count=3, min_read_seconds=8, max_read_seconds=15
        )

    assert [call.args[1] for call in reading.call_args_list] == [11.0, 11.0, 11.0]
