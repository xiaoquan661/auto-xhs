from __future__ import annotations

import json

from scripts.xhs.search import collect_search_feeds


def _raw_feed(feed_id: str, *, liked: bool = False) -> dict:
    return {
        "id": feed_id,
        "xsecToken": f"token-{feed_id}",
        "modelType": "note",
        "noteCard": {
            "displayTitle": f"标题 {feed_id}",
            "interactInfo": {"liked": liked, "collected": False},
        },
    }


class ScrollingSearchPage:
    def __init__(self) -> None:
        self.position = 0
        self.scrolls: list[tuple[int, int]] = []
        self.batches = [
            [_raw_feed("already", liked=True)],
            [_raw_feed("already", liked=True), _raw_feed("a")],
            [_raw_feed("already", liked=True), _raw_feed("a"), _raw_feed("b")],
        ]

    def navigate(self, _url: str) -> None:
        pass

    def wait_for_load(self) -> None:
        pass

    def wait_dom_stable(self) -> None:
        pass

    def evaluate(self, script: str):
        if script == "window.innerHeight || 768":
            return 800
        return json.dumps(self.batches[self.position], ensure_ascii=False)

    def scroll_by(self, x: int, y: int) -> None:
        self.scrolls.append((x, y))
        self.position = min(self.position + 1, len(self.batches) - 1)


def test_collect_search_feeds_scrolls_until_eligible_pool_is_full() -> None:
    page = ScrollingSearchPage()

    feeds, stats = collect_search_feeds(
        page,
        "露营",
        target_count=2,
        duration_seconds=30,
        accept=lambda feed: not feed.note_card.interact_info.liked,
        sleeper=lambda _seconds: None,
    )

    assert [feed.id for feed in feeds] == ["a", "b"]
    assert len(page.scrolls) == 2
    assert stats["scroll_count"] == 2
    assert stats["total_seen_count"] == 3
    assert stats["collection_stop_reason"] == "pool_reached"
