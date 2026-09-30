from __future__ import annotations

from itertools import pairwise
from unittest.mock import patch

import pytest
from xhs.errors import ActionResultUnknownError, InteractionStateError, XHSError
from xhs.home_engagement import home_engagement
from xhs.types import ActionResult, Feed, InteractInfo, NoteCard, User
from xhs.urls import HOME_URL


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    current = [0.0]

    def sleep(seconds):
        current[0] += seconds

    monkeypatch.setattr("time.monotonic", lambda: current[0])
    monkeypatch.setattr("time.time", lambda: current[0])
    monkeypatch.setattr("time.sleep", sleep)
    return current


def _feed(index: int, *, feed_type: str = "normal") -> Feed:
    return Feed(
        id=f"feed-{index}",
        xsec_token=f"token-{index}",
        model_type="note",
        index=index,
        note_card=NoteCard(
            type=feed_type,
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
    from xhs.progress import record_progress_with

    page = FakePage()
    feeds = [_feed(index) for index in range(8)]
    progress: list[dict] = []

    def read_with_progress_check(_page, _seconds, _deadline):
        assert sum(event["stage"] == "browsing" for event in progress) == int(
            page.current_feed.split("-")[-1]
        )
        return 10.0

    with (
        patch(
            "xhs.home_engagement._browse_candidates",
            side_effect=lambda _page, seen: [feed for feed in feeds if feed.id not in seen],
        ),
        record_progress_with(progress.append),
    ):
        result = home_engagement(
            page,
            browse_count=6,
            like_count=2,
            duration_seconds=600,
            reader=read_with_progress_check,
            liker=lambda _page, feed_id: ActionResult(feed_id, True, "点赞成功"),
            chooser=lambda candidates: candidates[0],
        )

    assert result["success"] is True
    assert [
        event["metrics"]["browse"]["actual"] for event in progress if event["stage"] == "browsing"
    ] == list(range(1, 7))
    assert result["counts"] == {"browsed": 6, "liked": 2, "skipped": 0}
    assert page.navigate_calls == [HOME_URL]
    assert len({item["feed_id"] for item in result["items"]}) == 6
    assert sum(item["like"] is not None for item in result["items"]) == 2
    assert all(item["xsec_token"].startswith("token-") for item in result["items"])
    assert all("comment" not in item for item in result["items"])


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
        patch(
            "xhs.home_engagement._browse_candidates",
            side_effect=lambda _page, seen: [feed for feed in feeds if feed.id not in seen],
        ),
        patch("xhs.home_engagement._open_from_feed", side_effect=open_with_one_failure),
        patch("xhs.browse_like.time.sleep"),
    ):
        result = home_engagement(
            page,
            browse_count=3,
            like_count=0,
            reader=lambda _page, _seconds, _deadline: 8.0,
            chooser=lambda candidates: candidates[0],
        )

    assert result["success"] is True
    assert result["counts"]["browsed"] == 3
    assert result["counts"]["skipped"] == 1
    assert result["skipped_items"][0]["status"] == "skipped"


def test_home_engagement_video_only_filters_non_video_candidates() -> None:
    page = FakePage()
    feeds = [
        _feed(1),
        _feed(2, feed_type="video"),
        _feed(3),
        _feed(4, feed_type="video"),
    ]

    with (
        patch(
            "xhs.home_engagement._browse_candidates",
            side_effect=lambda _page, seen: [feed for feed in feeds if feed.id not in seen],
        ),
        patch("xhs.browse_like.time.sleep"),
    ):
        result = home_engagement(
            page,
            browse_count=1,
            like_count=0,
            video_only=True,
            excluded_feed_ids=["feed-2"],
            reader=lambda _page, _seconds, _deadline: 8.0,
            chooser=lambda candidates: candidates[0],
        )

    assert result["success"] is True
    assert result["requested"]["video_only"] is True
    assert result["requested"]["excluded_feed_count"] == 1
    assert [item["feed_id"] for item in result["items"]] == ["feed-4"]
    assert {item["type"] for item in result["items"]} == {"video"}


def test_like_spacing_and_intervening_reads(clock) -> None:
    feeds = [_feed(index) for index in range(10)]
    calls = []

    def like(page, feed_id):
        calls.append((feed_id, clock[0]))
        return ActionResult(feed_id, True, "点赞成功", changed=True)

    with patch(
        "xhs.home_engagement._browse_candidates",
        side_effect=lambda p, seen: [feed for feed in feeds if feed.id not in seen],
    ):
        result = home_engagement(
            FakePage(),
            browse_count=10,
            like_count=2,
            duration_seconds=600,
            reader=lambda *args: 15,
            liker=like,
            chooser=lambda candidates: candidates[0],
        )
    assert result["success"]
    assert [item[0] for item in calls] == ["feed-0", "feed-3"]
    assert 90 <= calls[1][1] - calls[0][1] <= 180


def test_unknown_like_stops_batch_and_keeps_item_result() -> None:
    feeds = [_feed(index) for index in range(10)]
    with (
        patch("xhs.home_engagement._browse_candidates", return_value=feeds),
    ):

        def like(page, feed_id):
            raise ActionResultUnknownError("平台状态尚未确认")

        result = home_engagement(
            FakePage(),
            browse_count=10,
            like_count=2,
            reader=lambda *args: 15,
            liker=like,
            chooser=lambda candidates: candidates[0],
        )
    assert result["result_unknown"]
    assert result["stop_reason"] == "like_result_unknown"
    assert result["counts"]["liked"] == 0
    assert len(result["items"]) == 1
    assert result["items"][0]["like"]["status"] == "result_unknown"


def test_missing_pre_click_control_continues_to_next_note() -> None:
    feeds = [_feed(index) for index in range(3)]
    calls = []

    def like(_page, feed_id):
        calls.append(feed_id)
        if len(calls) == 1:
            raise InteractionStateError("点赞前未找到笔记操作按钮")
        return ActionResult(feed_id, True, "点赞成功", changed=True)

    with patch(
        "xhs.home_engagement._browse_candidates",
        side_effect=lambda _page, seen: [feed for feed in feeds if feed.id not in seen],
    ):
        result = home_engagement(
            FakePage(),
            browse_count=2,
            like_count=1,
            reader=lambda *_: 15,
            liker=like,
            chooser=lambda candidates: candidates[0],
        )
    assert result["counts"]["browsed"] == 2
    assert result["counts"]["liked"] == 1
    assert result["items"][0]["like"]["status"] == "failed"
    assert result["items"][1]["like"]["status"] == "success"


def test_deadline_preserves_partial_counts(clock) -> None:
    feeds = [_feed(index) for index in range(4)]
    calls = []
    with patch(
        "xhs.home_engagement._browse_candidates",
        side_effect=lambda p, seen: [feed for feed in feeds if feed.id not in seen],
    ):

        def like(page, feed_id):
            calls.append(feed_id)
            return ActionResult(feed_id, True, "点赞成功")

        result = home_engagement(
            FakePage(),
            browse_count=4,
            like_count=2,
            duration_seconds=30,
            reader=lambda *args: 15,
            liker=like,
            chooser=lambda candidates: candidates[0],
        )
    assert result["partial"]
    assert len(calls) == 1
    assert not result["result_unknown"]


def install_clock_and_feed(monkeypatch):
    now = [1000.0]
    feeds = [_feed(index) for index in range(50)]

    def advance(seconds):
        now[0] += seconds

    monkeypatch.setattr("time.time", lambda: now[0])
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    monkeypatch.setattr("time.sleep", advance)
    monkeypatch.setattr(
        "xhs.home_engagement._browse_candidates",
        lambda _page, seen: [feed for feed in feeds if feed.id not in seen],
    )
    monkeypatch.setattr("xhs.home_engagement._open_from_feed", lambda *_args: None)
    monkeypatch.setattr("xhs.home_engagement._close_detail", lambda *_args: "close_button")
    return now, advance


@pytest.mark.parametrize("upper", [False, True])
def test_daily_quota_respects_both_spacing_rules_and_dwell(monkeypatch, tmp_path, upper):
    now, advance = install_clock_and_feed(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda low, high: high if upper else low)
    likes = []
    reads = []

    def read(_page, seconds, _deadline):
        reads.append(seconds)
        advance(seconds)
        return seconds

    def like(_page, feed_id):
        likes.append((int(feed_id.split("-")[1]), now[0]))
        return ActionResult(feed_id, True, "点赞成功")

    result = home_engagement(
        FakePage(),
        browse_count=40,
        like_count=8,
        duration_seconds=3840,
        reader=read,
        liker=like,
        chooser=lambda candidates: candidates[0],
        pacing_path=tmp_path / "pacing.json",
    )
    assert result["success"] is True
    assert result["counts"] == {"browsed": 40, "liked": 8, "skipped": 0}
    assert all(15 <= seconds <= 45 for seconds in reads)
    for previous, current in pairwise(likes):
        assert current[0] - previous[0] >= 3
        assert current[1] - previous[1] >= (180 if upper else 90)


def test_next_task_keeps_spacing_after_failed_like(monkeypatch, tmp_path):
    now, advance = install_clock_and_feed(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda low, high: low)
    path = tmp_path / "pacing.json"
    attempts = []

    def read(_page, seconds, _deadline):
        advance(seconds)
        return seconds

    def like(_page, feed_id):
        attempts.append((feed_id, now[0]))
        return ActionResult(feed_id, len(attempts) > 1, "执行结果")

    first = home_engagement(
        FakePage(),
        browse_count=1,
        like_count=1,
        pacing_path=path,
        reader=read,
        liker=like,
        chooser=lambda candidates: candidates[0],
    )
    assert first["partial"] is True
    second = home_engagement(
        FakePage(),
        browse_count=3,
        like_count=1,
        pacing_path=path,
        excluded_feed_ids=["feed-0"],
        reader=read,
        liker=like,
        chooser=lambda candidates: candidates[0],
    )
    assert second["success"] is True
    assert attempts[1][0] == "feed-3"
    assert attempts[1][1] - attempts[0][1] >= 90


def test_deadline_during_like_wait_returns_partial_without_extra_like(monkeypatch):
    _now, advance = install_clock_and_feed(monkeypatch)
    monkeypatch.setattr("random.uniform", lambda low, high: low)
    attempts = []

    def read(_page, seconds, _deadline):
        advance(seconds)
        return seconds

    def like(_page, feed_id):
        attempts.append(feed_id)
        return ActionResult(feed_id, True, "点赞成功")

    result = home_engagement(
        FakePage(),
        browse_count=6,
        like_count=2,
        duration_seconds=60,
        reader=read,
        liker=like,
        chooser=lambda candidates: candidates[0],
    )
    assert result["stop_reason"] == "time_limit"
    assert result["partial"] is True
    assert attempts == ["feed-0"]


def test_reading_error_returns_completed_items_and_checkpoint(tmp_path):
    import json

    page = FakePage()
    feeds = [_feed(i) for i in range(3)]
    calls = [0]

    def reader(*args):
        calls[0] += 1
        if calls[0] == 2:
            raise XHSError("reading interrupted")
        return 20.0

    checkpoint = tmp_path / "progress.json"
    with patch(
        "xhs.home_engagement._browse_candidates",
        side_effect=lambda page, seen: [f for f in feeds if f.id not in seen],
    ):
        result = home_engagement(
            page,
            browse_count=3,
            like_count=0,
            reader=reader,
            chooser=lambda choices: choices[0],
            checkpoint_path=checkpoint,
        )
    assert result["partial"] is True
    assert result["counts"]["browsed"] == 1
    assert result["stop_reason"] == "reading_interrupted"
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["items"] == result["items"]


def test_frame_replacement_retries_only_reading_on_same_note(tmp_path):
    page = FakePage()
    calls = [0]

    def reader(*args):
        calls[0] += 1
        if calls[0] == 1:
            raise XHSError("Frame with ID 0 was removed.")
        return 20.0

    with patch("xhs.home_engagement._browse_candidates", return_value=[_feed(1)]):
        result = home_engagement(
            page,
            browse_count=1,
            like_count=0,
            reader=reader,
            checkpoint_path=tmp_path / "progress.json",
        )
    assert result["success"] is True
    assert calls[0] == 2
    assert result["counts"]["liked"] == 0
