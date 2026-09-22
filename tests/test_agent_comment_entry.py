from __future__ import annotations

import cli
import pytest
from capability_registry import get_capability_policy


def test_comment_commands_use_post_comment() -> None:
    parser = cli.build_parser()
    args = parser.parse_args([
        "post-comment", "--feed-id", "note", "--xsec-token", "token", "--content", "具体评论",
    ])
    assert args.func is cli.cmd_post_comment
    with pytest.raises(SystemExit):
        parser.parse_args(["random-comment", "--count", "1"])
    with pytest.raises(KeyError):
        get_capability_policy("random-comment")
