from __future__ import annotations

from unittest.mock import patch

from xhs.errors import XHSError
from xhs.home_engagement import home_engagement
from xhs.types import ActionResult, Feed, InteractInfo, NoteCard, User
from xhs.urls import HOME_URL


def _feed(index: int) -> Feed:
    return Feed(
        id=f"feed-{index}",
        xsec_token=f"token-{index}",
        model_type="note",
        index=index,
        note_card=NoteCard(
            type="normal",
            display_title=f"note-{index}",
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

    def scroll_by(self, x: int, y: int) -> None:
        self.scrolls.append((x, y))


def test_home_engagement_reuses_one_home_session_and_records_actions() -> None:
    page = FakePage()
    feeds = [_feed(index) for index in range(8)]
    comments: list[tuple[str, str]] = []

    with (
        patch("xhs.home_engagement._browse_candidates", side_effect=lambda _page, seen: [feed for feed in feeds if feed.id not in seen]),
        patch("xhs.browse_like.time.sleep"),
    ):
        result = home_engagement(
            page,
            browse_count=6,
            like_count=2,
            comment_count=1,
            reader=lambda _page, _seconds, _deadline: 10.0,
            liker=lambda _page, feed_id: ActionResult(feed_id, True, "点赞成功"),
            commenter=lambda _page, content, *, feed_id="": comments.append((feed_id, content)),
            chooser=lambda candidates: candidates[0],
        )

    assert result["success"] is True
    assert result["counts"] == {"browsed": 6, "liked": 2, "commented": 1, "skipped": 0}
    assert page.navigate_calls == [HOME_URL]
    assert len({item["feed_id"] for item in result["items"]}) == 6
    assert sum(item["like"] is not None for item in result["items"]) == 2
    assert sum(item["comment"] is not None for item in result["items"]) == 1
    assert comments[0][0] in {item["feed_id"] for item in result["items"]}


def test_home_engagement_skips_disappearing_card_and_continues() -> None:
    page = FakePage()
    feeds = [_feed(index) for index in range(5)]
    real_open = __import__("xhs.browse_like", fromlist=["_open_from_feed"])._open_from_feed
    attempts = 0

    def open_with_one_failure(target_page, feed):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise XHSError("信息流中未找到笔记卡片")
        return real_open(target_page, feed)

    with (
        patch("xhs.home_engagement._browse_candidates", side_effect=lambda _page, seen: [feed for feed in feeds if feed.id not in seen]),
        patch("xhs.home_engagement._open_from_feed", side_effect=open_with_one_failure),
        patch("xhs.browse_like.time.sleep"),
    ):
        result = home_engagement(
            page,
            browse_count=3,
            like_count=0,
            comment_count=0,
            reader=lambda _page, _seconds, _deadline: 8.0,
            chooser=lambda candidates: candidates[0],
        )

    assert result["success"] is True
    assert result["counts"]["browsed"] == 3
    assert result["counts"]["skipped"] == 1
    assert result["skipped_items"][0]["status"] == "skipped"
