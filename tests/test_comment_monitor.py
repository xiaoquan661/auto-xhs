from __future__ import annotations

from scripts.xhs.comment_monitor import collect_note_comments, collect_own_note_comments
from scripts.xhs.types import (
    Comment,
    CommentList,
    Feed,
    FeedDetailResponse,
    User,
    UserProfileResponse,
)


def test_monitor_normalizes_comments_and_skips_owner_replies() -> None:
    child = Comment(
        id="reply-1",
        note_id="note-1",
        content="补充问题",
        create_time=1_755_600_100_000,
        user_info=User(user_id="visitor-2", nickname="访客二"),
    )
    owner_reply = Comment(
        id="reply-owner",
        note_id="note-1",
        content="作者回复",
        create_time=1_755_600_200_000,
        user_info=User(user_id="owner-1", nickname="作者"),
    )
    parent = Comment(
        id="comment-1",
        note_id="note-1",
        content="怎么报名？",
        create_time=1_755_600_000_000,
        user_info=User(user_id="visitor-1", nickname="访客一"),
        sub_comments=[child, owner_reply],
    )

    def load(_page, _feed_id, _token):
        return FeedDetailResponse(comments=CommentList(list_=[parent]))

    result = collect_note_comments(
        object(),
        [{"feed_id": "note-1", "xsec_token": "token-1"}],
        owner_user_id="owner-1",
        detail_loader=load,
    )

    assert [item["comment_id"] for item in result["comments"]] == [
        "comment-1",
        "reply-1",
    ]
    assert result["comments"][1]["parent_comment_id"] == "comment-1"
    assert result["comments"][0]["occurred_at"].startswith("2025-")
    assert result["cursor"].endswith(":reply-1")


def test_monitor_returns_partial_result_when_one_note_fails() -> None:
    def load(_page, feed_id, _token):
        if feed_id == "note-bad":
            raise RuntimeError("笔记不可访问")
        return FeedDetailResponse(comments=CommentList())

    result = collect_note_comments(
        object(),
        [
            {"feed_id": "note-ok", "xsec_token": "token-1"},
            {"feed_id": "note-bad", "xsec_token": "token-2"},
        ],
        detail_loader=load,
    )

    assert result["partial"] is True
    assert result["failed_note_count"] == 1
    assert result["failures"][0]["feed_id"] == "note-bad"


def test_monitor_discovers_recent_notes_from_owner_profile() -> None:
    def profile(_page, user_id, _token):
        assert user_id == "owner-1"
        return UserProfileResponse(
            feeds=[
                Feed(id="note-1", xsec_token="token-1"),
                Feed(id="note-2", xsec_token="token-2"),
            ]
        )

    def detail(_page, feed_id, _token):
        return FeedDetailResponse(
            comments=CommentList(
                list_=[
                    Comment(
                        id=f"comment-{feed_id}",
                        note_id=feed_id,
                        content="新评论",
                        create_time=1_755_600_000,
                        user_info=User(user_id="visitor-1"),
                    )
                ]
            )
        )

    result = collect_own_note_comments(
        object(),
        owner_user_id="owner-1",
        max_notes=1,
        profile_loader=profile,
        detail_loader=detail,
    )

    assert result["discovered_note_count"] == 1
    assert result["comments"][0]["feed_id"] == "note-1"
