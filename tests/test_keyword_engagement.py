from __future__ import annotations

from scripts.xhs import keyword_engagement as module
from scripts.xhs.types import ActionResult, Feed, InteractInfo, NoteCard, User


def _feed(feed_id: str, *, liked: bool = False, collected: bool = False) -> Feed:
    return Feed(
        id=feed_id,
        xsec_token=f"token-{feed_id}",
        model_type="note",
        note_card=NoteCard(
            display_title=f"标题 {feed_id}",
            user=User(nickname=f"作者 {feed_id}"),
            interact_info=InteractInfo(liked=liked, collected=collected),
        ),
    )


def test_keyword_like_filters_existing_state_and_randomly_selects(monkeypatch) -> None:
    feeds = [_feed("a"), _feed("already", liked=True), _feed("b")]
    calls: list[str] = []
    monkeypatch.setattr(
        module,
        "collect_search_feeds",
        lambda *_args, **kwargs: (
            [feed for feed in feeds if kwargs["accept"](feed)],
            {
                "collected_count": 2,
                "total_seen_count": 3,
                "scroll_count": 2,
                "collection_elapsed_seconds": 3.2,
                "collection_stop_reason": "pool_reached",
            },
        ),
    )
    monkeypatch.setattr(
        module,
        "like_feed",
        lambda _page, feed_id, _token: (
            calls.append(feed_id) or ActionResult(feed_id, True, "点赞成功")
        ),
    )

    result = module.keyword_engagement(
        object(),
        keyword="露营",
        action="like",
        count=2,
        sampler=lambda population, count: list(population)[:count],
    )

    assert calls == ["a", "b"]
    assert result["candidate_count"] == 2
    assert result["succeeded_count"] == 2
    assert result["partial"] is False


def test_keyword_both_returns_per_action_partial_result(monkeypatch) -> None:
    monkeypatch.setattr(
        module,
        "collect_search_feeds",
        lambda *_args, **_kwargs: (
            [_feed("a")],
            {
                "collected_count": 1,
                "total_seen_count": 1,
                "scroll_count": 0,
                "collection_elapsed_seconds": 0.2,
                "collection_stop_reason": "no_new_results",
            },
        ),
    )
    monkeypatch.setattr(
        module,
        "like_feed",
        lambda *_args: ActionResult("a", True, "点赞成功"),
    )
    monkeypatch.setattr(
        module,
        "favorite_feed",
        lambda *_args: ActionResult("a", False, "收藏失败"),
    )

    result = module.keyword_engagement(
        object(),
        keyword="AI 视频",
        action="both",
        count=1,
    )

    assert result["partial"] is True
    assert result["items"][0]["actions"]["like"]["status"] == "success"
    assert result["items"][0]["actions"]["favorite"]["status"] == "failed"
