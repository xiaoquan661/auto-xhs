from __future__ import annotations

from scripts.xhs.metrics_collector import collect_operations_metrics, parse_count
from scripts.xhs.types import (
    Feed,
    InteractInfo,
    NoteCard,
    UserBasicInfo,
    UserInteraction,
    UserProfileResponse,
)


def test_parse_count_understands_plain_and_chinese_units() -> None:
    assert parse_count("1,234") == 1234
    assert parse_count("1.2万") == 12000
    assert parse_count("0.5亿") == 50000000
    assert parse_count(8) == 8
    assert parse_count("") is None


def test_collect_metrics_normalizes_account_and_note_values() -> None:
    def profile(_page, user_id, _token):
        assert user_id == "owner-1"
        return UserProfileResponse(
            user_basic_info=UserBasicInfo(nickname="品牌号", red_id="brand-1"),
            interactions=[
                UserInteraction(name="关注", count="25"),
                UserInteraction(name="粉丝", count="1.2万"),
                UserInteraction(name="获赞", count="8.6万"),
            ],
            feeds=[
                Feed(
                    id="note-1",
                    xsec_token="token-1",
                    note_card=NoteCard(
                        display_title="第一篇",
                        interact_info=InteractInfo(
                            liked_count="100",
                            collected_count="30",
                            comment_count="8",
                            shared_count="2",
                        ),
                    ),
                )
            ],
        )

    result = collect_operations_metrics(
        object(),
        owner_user_id="owner-1",
        profile_loader=profile,
    )

    assert result["account"]["metrics"]["followers"] == 12000
    assert result["account"]["metrics"]["following"] == 25
    assert result["account"]["metrics"]["likes"] == 86000
    assert result["notes"][0]["metrics"] == {
        "likes": 100,
        "favorites": 30,
        "comments": 8,
        "shares": 2,
    }
