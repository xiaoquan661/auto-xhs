from __future__ import annotations

from scripts.xhs import random_comment as module
from scripts.xhs.types import Feed, FeedDetail, FeedDetailResponse, NoteCard, User


def _feed(feed_id: str) -> Feed:
    return Feed(
        id=feed_id,
        xsec_token=f"token-{feed_id}",
        model_type="note",
        note_card=NoteCard(
            display_title=f"标题 {feed_id}",
            user=User(nickname=f"作者 {feed_id}"),
        ),
    )


def _detail(feed_id: str) -> FeedDetailResponse:
    return FeedDetailResponse(
        note=FeedDetail(
            note_id=feed_id,
            title=f"露营清单 {feed_id}",
            body="先检查天气，再准备防潮垫和照明设备。出发前确认营地规则。",
            user=User(nickname=f"作者 {feed_id}"),
        )
    )


def test_comment_content_uses_note_title_and_body() -> None:
    note = _detail("a").note

    content = module.generate_comment_content(
        note,
        style="natural",
        chooser=lambda choices: choices[-1],
    )

    assert "露营清单 a" in content
    assert "先检查天气" in content


def test_random_comment_selects_home_candidates_and_directly_posts(monkeypatch) -> None:
    feeds = [_feed("a"), _feed("b"), _feed("c")]
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        module,
        "collect_home_feeds",
        lambda *_args, **_kwargs: (
            feeds,
            {
                "collected_count": 3,
                "total_seen_count": 3,
                "scroll_count": 2,
                "collection_elapsed_seconds": 1.5,
                "collection_stop_reason": "pool_reached",
            },
        ),
    )

    result = module.random_comment(
        object(),
        count=2,
        sampler=lambda population, count: list(population)[:count],
        detail_reader=lambda _page, feed_id, _token: _detail(feed_id),
        comment_sender=lambda _page, feed_id, _token, content: sent.append(
            (feed_id, content)
        ),
        content_generator=lambda note, *, style: f"{style}:{note.title}",
    )

    assert [feed_id for feed_id, _content in sent] == ["a", "b"]
    assert result["result_type"] == "random_comment"
    assert result["succeeded_count"] == 2
    assert result["partial"] is False
    assert result["items"][0]["content"] == "natural:露营清单 a"


def test_random_comment_keeps_per_note_failure_result(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "collect_home_feeds",
        lambda *_args, **_kwargs: (
            [_feed("a"), _feed("b")],
            {
                "collected_count": 2,
                "total_seen_count": 2,
                "scroll_count": 0,
                "collection_elapsed_seconds": 0.2,
                "collection_stop_reason": "pool_reached",
            },
        ),
    )

    def send(_page, feed_id, _token, _content):
        if feed_id == "b":
            raise RuntimeError("评论按钮不可用")

    result = module.random_comment(
        object(),
        count=2,
        style="question",
        sampler=lambda population, count: list(population)[:count],
        detail_reader=lambda _page, feed_id, _token: _detail(feed_id),
        comment_sender=send,
        content_generator=lambda note, *, style: f"{style}:{note.title}",
    )

    assert result["partial"] is True
    assert result["succeeded_count"] == 1
    assert result["failed_count"] == 1
    assert result["items"][1]["status"] == "failed"
    assert result["items"][1]["message"] == "评论按钮不可用"


def test_random_comment_rejects_more_than_three_posts() -> None:
    try:
        module.random_comment(object(), count=4)
    except ValueError as exc:
        assert "1 到 3" in str(exc)
    else:
        raise AssertionError("count=4 should be rejected")
