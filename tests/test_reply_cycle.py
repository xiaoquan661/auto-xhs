from __future__ import annotations

from scripts.application_service import ApplicationService
from scripts.operations_db import OperationsDatabase
from scripts.product_store import ProductStore
from scripts.reply_intelligence_service import ReplyIntelligenceService
from scripts.xhs.direct_message import PrivateMessageContextChangedError


class FakeReplyClient:
    def status(self) -> dict:
        return {"configured": True, "model": "fake", "missing": []}

    def generate(self, **_values) -> dict:
        return {
            "content": (
                '{"reply":"可以，按笔记里的步骤操作就好～",'
                '"intent":"question","confidence":0.95,'
                '"reason":"笔记已有明确说明",'
                '"facts_used":["笔记步骤"],"missing_information":[]}'
            ),
            "model": "fake",
        }


class FakeRunner:
    def execute(self, _account: str, capability: str, _parameters: dict) -> dict:
        if capability == "collect-note-comments":
            return {
                "comments": [
                    {
                        "notification_id": "notice-1",
                        "comment_id": "comment-1",
                        "feed_id": "note-1",
                        "user_id": "user-1",
                        "nickname": "小红",
                        "content": "这个周末可以参加吗？",
                        "note_title": "周末活动",
                        "note_content": "周六举行，报名步骤见正文。",
                        "is_own_note": True,
                        "occurred_at": "2026-08-27T10:00:00+00:00",
                        "source": "notifications",
                    }
                ],
                "count": 1,
                "cursor": "notice-1",
                "last_seen_time": "2026-08-27T10:00:00+00:00",
            }
        if capability == "reply-comment":
            return {
                "success": True,
                "message": "回复成功",
                "readback": {
                    "notification_id": "notice-1",
                    "reply_present": True,
                },
            }
        raise AssertionError(f"unexpected capability: {capability}")


class StalePrivateMessageRunner:
    def execute(self, _account: str, capability: str, _parameters: dict) -> dict:
        if capability == "collect-private-messages":
            return {
                "messages": [
                    {
                        "message_id": "user-1:2:最近怎么样？",
                        "user_id": "user-1",
                        "nickname": "小红",
                        "content": "最近怎么样？",
                        "peer_turn": ["最近怎么样？"],
                        "context": [
                            {"role": "self", "content": "好久不见"},
                            {"role": "peer", "content": "最近怎么样？"},
                        ],
                        "message_count": 2,
                        "occurred_at": "2026-08-27T10:00:00+00:00",
                    }
                ],
                "count": 1,
                "cursor": "user-1:2:最近怎么样？",
                "last_seen_time": "2026-08-27T10:00:00+00:00",
            }
        if capability == "send-private-messages":
            raise PrivateMessageContextChangedError()
        raise AssertionError(f"unexpected capability: {capability}")


def test_reply_cycle_collects_generates_sends_and_records_readback(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=database,
        reply_intelligence=ReplyIntelligenceService(FakeReplyClient()),
        business_runner=FakeRunner(),
    )
    service._resolve_account_platform = lambda _account: "xiaohongshu"
    service.tasks._platform_resolver = lambda _account: "xiaohongshu"
    service.get_account_status = lambda _account: {
        "ready": True,
        "identity": {"live_user_id": "owner-1"},
        "next_action": "",
    }
    rule = service.reply_rules.create(
        account_slot="alpha",
        private_message_enabled=False,
    )
    enabled_rule = service.reply_rules.set_enabled(rule["rule_id"], True)

    result = service.run_reply_cycle(
        account_slot="alpha",
        channels=["comment"],
        max_events=10,
    )

    assert result["cycle"]["result_state"] == "SUCCESS"
    assert result["cycle"]["summary"]["reply"] == 1
    assert result["decisions"][0]["decision"] == "reply"
    assert result["decisions"][0]["result_state"] == "SUCCESS"
    assert result["decisions"][0]["rule_id"] == enabled_rule["rule_id"]
    assert result["decisions"][0]["rule_version"] == enabled_rule["rule_version"]
    records = service.reply_records.list(account_slot="alpha")
    assert len(records) == 1
    assert records[0]["send_state"] == "CONFIRMED"
    assert any(row["event_type"] == "outbound_reply" for row in result["incremental_data"])
    assert records[0]["readback_state"] == "VERIFIED"
    assert records[0]["rule_id"] == enabled_rule["rule_id"]
    assert records[0]["rule_version"] == enabled_rule["rule_version"]
    service.reply_rules.update(rule["rule_id"], reply_style="professional")
    historical = service.reply_rules.get_version(
        enabled_rule["rule_id"],
        result["decisions"][0]["rule_version"],
    )
    assert historical["reply_style"] == "natural"
    assert service.inbound_events.list(account_slot="alpha")[0]["handling_state"] == "HANDLED"


def test_inspection_collects_all_channels_when_reply_disabled(tmp_path) -> None:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(tmp_path),
    )
    service._resolve_account_platform = lambda _account: "xiaohongshu"
    service.tasks._platform_resolver = lambda _account: "xiaohongshu"
    service.get_account_status = lambda _account: {
        "ready": True, "identity": {"live_user_id": "owner-1"},
    }
    service.reply_rules.create(account_slot="alpha")
    calls = []

    def collect(task_id):
        task = service.tasks.get(task_id)
        calls.append(task["capability"])
        return {"task": {**task, "state": "SUCCESS"}, "result": {"count": 0}}

    service.execute_task = collect
    result = service.run_reply_cycle(account_slot="alpha", inspection=True)
    assert calls == ["collect-operations-metrics", "collect-note-comments",
                     "collect-private-messages"]
    assert result["decisions"] == []
    assert len(result["incremental_data"]) == 4


def test_reply_cycle_routes_changed_private_message_context_to_pending(tmp_path) -> None:
    database = OperationsDatabase(path=tmp_path / "operations.db")
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=database,
        reply_intelligence=ReplyIntelligenceService(FakeReplyClient()),
        business_runner=StalePrivateMessageRunner(),
    )
    service._resolve_account_platform = lambda _account: "xiaohongshu"
    service.tasks._platform_resolver = lambda _account: "xiaohongshu"
    service.get_account_status = lambda _account: {
        "ready": True,
        "identity": {"live_user_id": "owner-1"},
        "next_action": "",
    }
    rule = service.reply_rules.create(
        account_slot="alpha",
        comment_enabled=False,
    )
    service.reply_rules.set_enabled(rule["rule_id"], True)

    result = service.run_reply_cycle(
        account_slot="alpha",
        channels=["private_message"],
        max_events=10,
    )

    decision = result["decisions"][0]
    assert decision["decision"] == "pending"
    assert decision["reason"] == "conversation_changed"
    assert decision["result_state"] == "FAILED"
    record = service.reply_records.list(account_slot="alpha")[0]
    assert record["error_code"] == "PRIVATE_MESSAGE_CONTEXT_CHANGED"
    assert record["readback_state"] == "MISSING"
