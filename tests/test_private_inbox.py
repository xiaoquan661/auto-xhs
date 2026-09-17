from __future__ import annotations

import json

import pytest

from scripts.xhs.private_inbox import collect_private_messages


class FakePage:
    def __init__(self) -> None:
        self.url = ""

    def navigate(self, url: str) -> None:
        self.url = url

    def wait_for_load(self) -> None:
        return None

    def wait_dom_stable(self) -> None:
        return None

    def evaluate(self, script: str):
        if ".xhs-im-conv-item" in script:
            return [
                {"user_id": "user-1", "nickname": "甲", "display_time": "刚刚", "preview": "你好"},
                {"user_id": "user-2", "nickname": "乙", "display_time": "昨天", "preview": "收到"},
            ]
        if ".chat-item" in script:
            messages = (
                [{"role": "peer", "content": "你好"}]
                if self.url.endswith("user-1")
                else [{"role": "peer", "content": "请问"}, {"role": "self", "content": "收到"}]
            )
            return json.dumps(
                {
                    "available": True,
                    "url": self.url,
                    "target_nickname": "甲" if self.url.endswith("user-1") else "乙",
                    "first_message_notice": False,
                    "messages": messages,
                },
                ensure_ascii=False,
            )
        raise AssertionError(script)


def test_private_history_owner_mismatch_rejects_batch(monkeypatch) -> None:
    from scripts.xhs.private_inbox import PrivateHistoryOwnerMismatchError

    monkeypatch.setattr(
        "scripts.xhs.private_history.collect_message_history",
        lambda *_: {"available": True, "owner_uid": "other-account"},
    )
    with pytest.raises(PrivateHistoryOwnerMismatchError, match="本批数据未入库"):
        collect_private_messages(FakePage(), expected_owner_uid="bound-account")


def test_private_inbox_only_returns_conversations_last_authored_by_peer() -> None:
    result = collect_private_messages(FakePage(), max_conversations=10)

    assert result["conversation_count"] == 2
    assert result["count"] == 1
    assert result["messages"][0]["user_id"] == "user-1"
    assert result["messages"][0]["content"] == "你好"
    assert len(result["conversation_snapshots"]) == 2
    second = result["conversation_snapshots"][1]
    assert second["user_id"] == "user-2"
    assert second["latest_role"] == "self"
    assert second["message_count"] == 2
    assert result["skipped"] == [
        {"user_id": "user-2", "reason": "latest_message_not_peer"}
    ]


@pytest.mark.parametrize("empty_confirmed", [False, True])
def test_empty_inbox_requires_visible_empty_state(empty_confirmed: bool) -> None:
    class EmptyPage(FakePage):
        def evaluate(self, script: str):
            if "const markers" in script:
                return empty_confirmed
            if "node.scrollTop" in script:
                return False
            return []

    result = collect_private_messages(EmptyPage())
    assert result["count"] == (0 if empty_confirmed else None)
    assert result["conversation_count"] == (0 if empty_confirmed else None)
    assert result["partial"] is not empty_confirmed
    assert result["status"] == ("SUCCESS" if empty_confirmed else "UNKNOWN")
    assert result["collection_batches"][0]["coverage"] == (
        "complete" if empty_confirmed else "unknown"
    )
