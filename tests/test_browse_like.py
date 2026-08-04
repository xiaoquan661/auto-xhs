from __future__ import annotations

from unittest.mock import patch

from xhs.browse_like import browse_like_cycle
from xhs.types import ActionResult, Feed, InteractInfo, NoteCard, User
from xhs.urls import HOME_URL


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


def test_browse_like_cycle_keeps_one_feed_session() -> None:
    page = FakePage()
    feeds = [_feed(index) for index in range(5)]

    with (
        patch("xhs.browse_like.extract_current_feeds", return_value=feeds),
        patch("xhs.browse_like.random.sample", return_value=feeds[:3]),
        patch("xhs.browse_like.time.sleep"),
        patch(
            "xhs.browse_like.like_current_feed",
            side_effect=lambda _page, feed_id: ActionResult(feed_id, True, "点赞成功"),
        ),
    ):
        result = browse_like_cycle(page, count=3, min_interval=1, max_interval=1)

    assert page.navigate_calls == [HOME_URL]
    assert len(page.scrolls) == 2
    assert len([item for item in page.clicked if "/explore/" in item]) == 3
    assert page.clicked.count(".close-circle") == 3
    assert result["count"] == 3
    assert result["refreshed_between_items"] is False
