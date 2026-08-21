from __future__ import annotations

import json

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


def test_private_inbox_only_returns_conversations_last_authored_by_peer() -> None:
    result = collect_private_messages(FakePage(), max_conversations=10)

    assert result["conversation_count"] == 2
    assert result["count"] == 1
    assert result["messages"][0]["user_id"] == "user-1"
    assert result["messages"][0]["content"] == "你好"
    assert result["skipped"] == [
        {"user_id": "user-2", "reason": "latest_message_not_peer"}
    ]
