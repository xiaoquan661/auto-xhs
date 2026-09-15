from __future__ import annotations

from scripts.application_service import ApplicationService
from scripts.conversation_ownership_service import OWNER_CODEX
from scripts.operations_db import OperationsDatabase
from scripts.product_store import ProductStore
from scripts.reply_intelligence_service import ReplyIntelligenceService
from scripts.xhs.direct_message import (
    PrivateMessageContextChangedError,
    PrivateMessageResultUnknownError,
)
from scripts.xhs.notifications import NotificationReplyError


class NoModelClient:
    def status(self) -> dict:
        return {"configured": False, "model": "", "missing": ["model"]}

    def generate(self, **_values) -> dict:
        raise AssertionError("Codex 回复收件箱不应调用外部回复模型")


class CommentRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def execute(self, _account: str, capability: str, parameters: dict) -> dict:
        self.calls.append((capability, parameters))
        if capability == "collect-note-comments":
            return {
                "comments": [
                    {
                        "notification_id": "notice-1",
                        "comment_id": "comment-1",
                        "feed_id": "note-1",
                        "user_id": "user-1",
                        "nickname": "小红",
                        "content": "周末可以参加吗？",
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
                "readback": "editor-closed",
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


class ResultUnknownPrivateMessageRunner(StalePrivateMessageRunner):
    def execute(self, account: str, capability: str, parameters: dict) -> dict:
        if capability == "send-private-messages":
            raise PrivateMessageResultUnknownError()
        return super().execute(account, capability, parameters)


class DelayedReadbackPrivateMessageRunner:
    def execute(self, _account: str, capability: str, _parameters: dict) -> dict:
        if capability != "collect-private-messages":
            raise AssertionError(f"unexpected capability: {capability}")
        return {
            "messages": [],
            "conversation_snapshots": [
                {
                    "user_id": "user-1",
                    "nickname": "小红",
                    "message_count": 3,
                    "context": [
                        {"role": "self", "content": "好久不见"},
                        {"role": "peer", "content": "最近怎么样？"},
                        {"role": "self", "content": "你好呀～"},
                    ],
                    "latest_role": "self",
                }
            ],
            "count": 0,
            "conversation_count": 1,
            "cursor": "",
            "last_seen_time": "2026-08-27T10:02:00+00:00",
        }


class MutablePrivateMessageRunner:
    def __init__(self, messages: list[dict]) -> None:
        self.messages = messages

    def execute(self, _account: str, capability: str, _parameters: dict) -> dict:
        if capability != "collect-private-messages":
            raise AssertionError(f"unexpected capability: {capability}")
        return {
            "messages": self.messages,
            "count": len(self.messages),
            "cursor": str(self.messages[-1]["message_id"] if self.messages else ""),
            "last_seen_time": (str(self.messages[-1]["occurred_at"] if self.messages else "")),
        }


class RetryableCommentRunner(CommentRunner):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    def execute(self, account: str, capability: str, parameters: dict) -> dict:
        if capability == "reply-comment" and not self.failed_once:
            self.calls.append((capability, parameters))
            self.failed_once = True
            raise NotificationReplyError("当前可见通知中没有目标评论")
        return super().execute(account, capability, parameters)


def _service(tmp_path, runner: CommentRunner) -> ApplicationService:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
        reply_intelligence=ReplyIntelligenceService(NoModelClient()),
        business_runner=runner,
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
        account_profile="像本人一样简短回复。",
        knowledge="周六活动可以报名。",
    )
    service.reply_rules.set_enabled(rule["rule_id"], True)
    return service


def _private_service(tmp_path, runner) -> ApplicationService:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
        reply_intelligence=ReplyIntelligenceService(NoModelClient()),
        business_runner=runner,
    )
    service._resolve_account_platform = lambda _account: "xiaohongshu"
    service.tasks._platform_resolver = lambda _account: "xiaohongshu"
    service.get_account_status = lambda _account: {
        "ready": True,
        "identity": {"live_user_id": "owner-1"},
        "next_action": "",
    }
    rule = service.reply_rules.create(account_slot="alpha", comment_enabled=False)
    service.reply_rules.set_enabled(rule["rule_id"], True)
    return service


def test_reply_inbox_collects_and_returns_full_context_for_codex(tmp_path) -> None:
    runner = CommentRunner()
    service = _service(tmp_path, runner)

    collected = service.collect_reply_inbox(
        "alpha",
        channels=["comment"],
        limit=10,
    )

    assert collected["count"] == 1
    event_id = collected["events"][0]["event_id"]
    context = service.get_reply_inbox_context("alpha", event_id)
    assert context["event"]["payload"]["note_title"] == "周末活动"
    assert context["event"]["payload"]["content"] == "周末可以参加吗？"
    assert context["policy"]["account_profile"] == "像本人一样简短回复。"
    assert context["policy"]["knowledge"] == "周六活动可以报名。"


def test_codex_reply_decision_sends_without_calling_external_model(tmp_path) -> None:
    runner = CommentRunner()
    service = _service(tmp_path, runner)
    event_id = service.collect_reply_inbox(
        "alpha",
        channels=["comment"],
    )["events"][0]["event_id"]

    result = service.apply_codex_reply_decision(
        "alpha",
        {
            "event_id": event_id,
            "action": "reply",
            "reply": "可以呀，周六按笔记里的步骤报名就好～",
            "intent": "question",
            "confidence": 0.96,
            "reason": "笔记和账号知识都有明确报名信息",
            "facts_used": ["周六举行", "笔记包含报名步骤"],
            "missing_information": [],
        },
    )

    assert result["decision"]["decision"] == "reply"
    assert result["task"]["state"] == "SUCCESS"
    assert result["cycle"]["summary"]["source"] == "codex"
    assert [call[0] for call in runner.calls].count("reply-comment") == 1
    assert service.inbound_events.get(event_id)["handling_state"] == "HANDLED"
    record = service.reply_records.list(account_slot="alpha")[0]
    assert record["readback_state"] == "VERIFIED"
    assert record["readback"] == {"acknowledgement": "editor-closed"}


def test_agent_can_reply_with_a_clarifying_question(tmp_path) -> None:
    runner = CommentRunner()
    service = _service(tmp_path, runner)
    event_id = service.collect_reply_inbox(
        "alpha",
        channels=["comment"],
    )["events"][0]["event_id"]

    result = service.apply_codex_reply_decision(
        "alpha",
        {
            "event_id": event_id,
            "action": "reply",
            "reply": "你想参加哪一场？",
            "intent": "question",
            "confidence": 0.8,
            "reason": "Codex 判断为普通问题",
            "facts_used": [],
            "missing_information": [],
        },
    )

    assert result["decision"]["decision"] == "reply"
    assert result["decision"]["reason"] == "sent_with_readback"
    assert [call[0] for call in runner.calls].count("reply-comment") == 1
    assert service.inbound_events.get(event_id)["handling_state"] == "HANDLED"


def test_codex_reply_can_retry_explicit_pre_send_failure(tmp_path) -> None:
    runner = RetryableCommentRunner()
    service = _service(tmp_path, runner)
    event_id = service.collect_reply_inbox(
        "alpha",
        channels=["comment"],
    )["events"][0]["event_id"]
    decision = {
        "event_id": event_id,
        "action": "reply",
        "reply": "可以呀，周六按笔记里的步骤报名就好～",
        "intent": "question",
        "confidence": 0.96,
        "reason": "笔记和账号知识都有明确报名信息",
        "facts_used": ["周六举行", "笔记包含报名步骤"],
        "missing_information": [],
    }

    failed = service.apply_codex_reply_decision("alpha", decision)
    retried = service.apply_codex_reply_decision("alpha", decision)

    assert failed["task"]["state"] == "FAILED"
    assert retried["task"]["state"] == "SUCCESS"
    assert failed["task"]["task_id"] != retried["task"]["task_id"]
    assert service.inbound_events.get(event_id)["handling_state"] == "HANDLED"


def test_codex_private_message_reply_stops_when_peer_turn_changed(tmp_path) -> None:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
        reply_intelligence=ReplyIntelligenceService(NoModelClient()),
        business_runner=StalePrivateMessageRunner(),
    )
    service._resolve_account_platform = lambda _account: "xiaohongshu"
    service.tasks._platform_resolver = lambda _account: "xiaohongshu"
    service.get_account_status = lambda _account: {
        "ready": True,
        "identity": {"live_user_id": "owner-1"},
        "next_action": "",
    }
    rule = service.reply_rules.create(account_slot="alpha", comment_enabled=False)
    service.reply_rules.set_enabled(rule["rule_id"], True)
    event_id = service.collect_reply_inbox(
        "alpha",
        channels=["private_message"],
    )["events"][0]["event_id"]

    result = service.apply_codex_reply_decision(
        "alpha",
        {
            "event_id": event_id,
            "action": "reply",
            "reply": "挺好的，你最近呢？",
            "intent": "ordinary",
            "confidence": 0.9,
            "reason": "回应对方的近况问候",
            "facts_used": [],
            "missing_information": [],
        },
    )

    assert result["decision"]["decision"] == "pending"
    assert result["decision"]["reason"] == "conversation_changed"
    assert result["task"]["error_code"] == "PRIVATE_MESSAGE_CONTEXT_CHANGED"


def test_codex_private_message_result_unknown_stays_pending_with_original_task(tmp_path) -> None:
    service = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
        reply_intelligence=ReplyIntelligenceService(NoModelClient()),
        business_runner=ResultUnknownPrivateMessageRunner(),
    )
    service._resolve_account_platform = lambda _account: "xiaohongshu"
    service.tasks._platform_resolver = lambda _account: "xiaohongshu"
    service.get_account_status = lambda _account: {
        "ready": True,
        "identity": {"live_user_id": "owner-1"},
        "next_action": "",
    }
    rule = service.reply_rules.create(account_slot="alpha", comment_enabled=False)
    service.reply_rules.set_enabled(rule["rule_id"], True)
    event_id = service.collect_reply_inbox(
        "alpha",
        channels=["private_message"],
    )["events"][0]["event_id"]

    result = service.apply_codex_reply_decision(
        "alpha",
        {
            "event_id": event_id,
            "action": "reply",
            "reply": "你好呀～",
            "intent": "ordinary",
            "confidence": 0.95,
            "reason": "回应普通问候",
            "facts_used": [],
            "missing_information": [],
        },
    )

    event = service.inbound_events.get(event_id)
    assert result["decision"]["decision"] == "pending"
    assert result["decision"]["reason"] == "result_unknown"
    assert result["task"]["state"] == "RESULT_UNKNOWN"
    assert event["handling_state"] == "TASK_CREATED"
    assert event["created_task_id"] == result["task"]["task_id"]
    conversation = service.list_private_message_conversations("alpha", owner_state=OWNER_CODEX)[
        "conversations"
    ][0]
    assert conversation["user_id"] == "user-1"
    assert conversation["handoff_event_id"] == event_id

    service.runner = DelayedReadbackPrivateMessageRunner()
    recollected = service.collect_reply_inbox("alpha", channels=["private_message"])

    assert recollected["count"] == 0
    inbound = recollected["collection_results"][0]["result"]["inbound_events"]
    assert inbound["resumed_count"] == 1
    assert len(inbound["reconciled_outbound"]) == 1
    assert service.tasks.get(result["task"]["task_id"])["state"] == "SUCCESS"
    assert service.inbound_events.get(event_id)["handling_state"] == "HANDLED"
    reconciled_record = service.reply_records.list(account_slot="alpha")[0]
    assert reconciled_record["result_state"] == "SUCCESS"
    assert reconciled_record["readback_state"] == "VERIFIED"
    operation_event = service.operation_events.get_by_task(result["task"]["task_id"])
    assert operation_event["result_state"] == "SUCCESS"
    reconciled_decision = service.reply_cycles.list_decisions(account_slot="alpha")[0]
    assert reconciled_decision["decision"] == "reply"
    assert reconciled_decision["reason"] == "sent_with_delayed_readback"
    conversation = service.private_message_conversations.get("alpha", "xiaohongshu", "user-1")
    assert conversation["owner_state"] == OWNER_CODEX
    assert conversation["resume_source"] == "CODEX_OUTBOUND"


def test_agent_replies_to_contact_question_and_receives_followups(tmp_path) -> None:
    runner = MutablePrivateMessageRunner(
        [
            {
                "message_id": "user-1:2:可以加个联系方式吗",
                "user_id": "user-1",
                "nickname": "小红",
                "content": "可以加个联系方式吗",
                "peer_turn": ["可以加个联系方式吗"],
                "context": [
                    {"role": "self", "content": "你好"},
                    {"role": "peer", "content": "可以加个联系方式吗"},
                ],
                "message_count": 2,
                "occurred_at": "2026-08-27T10:00:00+00:00",
            }
        ]
    )
    service = _private_service(tmp_path, runner)
    event_id = service.collect_reply_inbox("alpha", channels=["private_message"])["events"][0][
        "event_id"
    ]

    service.apply_codex_reply_decision(
        "alpha",
        {
            "event_id": event_id,
            "action": "reply",
            "reply": "你可以在这里告诉我具体想聊什么。",
            "intent": "contact_details",
            "reason": "回应联系方式问题",
        },
    )
    conversation = service.private_message_conversations.get("alpha", "xiaohongshu", "user-1")
    assert conversation["owner_state"] == OWNER_CODEX

    runner.messages = [
        {
            "message_id": "user-1:3:在吗",
            "user_id": "user-1",
            "nickname": "小红",
            "content": "在吗",
            "peer_turn": ["在吗"],
            "context": [
                {"role": "self", "content": "你好"},
                {"role": "peer", "content": "可以加个联系方式吗"},
                {"role": "peer", "content": "在吗"},
            ],
            "message_count": 3,
            "occurred_at": "2026-08-27T10:01:00+00:00",
        }
    ]
    while_human = service.collect_reply_inbox("alpha", channels=["private_message"])
    assert while_human["count"] == 1
    inbound = while_human["collection_results"][0]["result"]["inbound_events"]

    runner.messages = [
        {
            "message_id": "user-1:5:你的桌面很好看",
            "user_id": "user-1",
            "nickname": "小红",
            "content": "你的桌面很好看",
            "peer_turn": ["你的桌面很好看"],
            "context": [
                {"role": "peer", "content": "可以加个联系方式吗"},
                {"role": "peer", "content": "在吗"},
                {"role": "self", "content": "你可以直接在这里聊"},
                {"role": "peer", "content": "你的桌面很好看"},
            ],
            "message_count": 5,
            "occurred_at": "2026-08-27T10:02:00+00:00",
        }
    ]
    resumed = service.collect_reply_inbox("alpha", channels=["private_message"])

    assert resumed["count"] == 2
    inbound = resumed["collection_results"][0]["result"]["inbound_events"]
    assert inbound["resumed_count"] == 0
    context = service.get_reply_inbox_context("alpha", resumed["events"][0]["event_id"])
    assert context["conversation_ownership"]["owner_state"] == OWNER_CODEX
    assert context["conversation_ownership"]["owner_state"] == OWNER_CODEX
