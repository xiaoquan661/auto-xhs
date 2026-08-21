from __future__ import annotations

import pytest

from scripts.application_service import ApplicationService, ServiceError
from scripts.operations_db import OperationsDatabase
from scripts.product_store import ProductStore
from scripts.reply_intelligence_service import ReplyIntelligenceService
from scripts.reply_llm_client import OpenAICompatibleReplyClient, ReplyModelConfig


class FakeReplyClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    def status(self) -> dict:
        return {"configured": True, "model": "fake-reply-model", "missing": []}

    def generate(self, **values) -> dict:
        self.calls.append(values)
        return {"content": self.content, "model": "fake-reply-model"}


def _event() -> dict:
    return {
        "event_id": "event-1",
        "account_slot": "alpha",
        "event_type": "note_comment",
        "platform_event_id": "comment-1",
        "object_id": "note-1",
        "payload": {
            "comment_id": "comment-1",
            "feed_id": "note-1",
            "nickname": "小红",
            "content": "周末也可以报名吗？",
            "parent_comment_id": "parent-1",
            "parent_comment_content": "报名截止到周五",
            "note_title": "周末手作活动",
            "note_content": "活动周六下午举行，报名方式见置顶说明。",
            "note_tags": ["手作", "周末活动"],
        },
    }


def test_intelligent_reply_uses_note_thread_profile_and_knowledge(tmp_path) -> None:
    client = FakeReplyClient(
        "```json\n"
        '{"reply":"可以呀，周六下午场也能报名，按置顶说明操作就好～",'
        '"intent":"question","confidence":0.91,"reason":"笔记给出了活动时间和报名入口"}'
        "\n```"
    )
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    (profile_root / "alpha.md").write_text("语气自然，称呼简短。", encoding="utf-8")
    (profile_root / "alpha.knowledge.md").write_text(
        "周六下午场正常接受报名。", encoding="utf-8"
    )
    service = ReplyIntelligenceService(client, profile_root=profile_root)

    result = service.generate_for_event(_event(), recent_replies=["欢迎来参加～"])

    assert result["reply"].startswith("可以呀")
    assert result["intent"] == "question"
    assert result["confidence"] == 0.91
    assert result["manual_review_required"] is True
    assert result["context_summary"]["profile_loaded"] is True
    prompt = client.calls[0]["user_prompt"]
    assert "周末手作活动" in prompt
    assert "报名截止到周五" in prompt
    system_prompt = client.calls[0]["system_prompt"]
    assert "语气自然" in system_prompt
    assert "周六下午场正常接受报名" in system_prompt


def test_intelligent_reply_marks_ai_style_and_recent_duplicate() -> None:
    client = FakeReplyClient(
        '{"reply":"希望对你有所帮助", "intent":"other", '
        '"confidence":0.6, "reason":"通用回复"}'
    )
    service = ReplyIntelligenceService(client)

    result = service.generate_for_event(
        _event(), recent_replies=["希望 对你 有所帮助"]
    )

    assert result["quality_flags"] == ["ai_style_phrase", "duplicate_recent_reply"]


def test_intelligent_private_message_reply_uses_conversation_context() -> None:
    client = FakeReplyClient(
        '{"reply":"最近挺好的，你呢？","intent":"discussion",'
        '"confidence":0.9,"reason":"回应对方问候"}'
    )
    service = ReplyIntelligenceService(client)
    event = {
        "event_id": "dm-1",
        "account_slot": "alpha",
        "event_type": "private_message",
        "actor_user_id": "user-1",
        "payload": {
            "user_id": "user-1",
            "nickname": "小红",
            "content": "最近怎么样？",
            "context": [
                {"role": "self", "content": "好久不见"},
                {"role": "peer", "content": "最近怎么样？"},
            ],
        },
    }

    result = service.generate_for_event(event)

    assert result["context_summary"]["channel"] == "private_message"
    assert "最近怎么样" in client.calls[0]["user_prompt"]
    assert "一条真实私信" in client.calls[0]["system_prompt"]


def test_intelligent_reply_rejects_invalid_model_output() -> None:
    service = ReplyIntelligenceService(FakeReplyClient("不是 JSON"))

    with pytest.raises(ServiceError) as exc_info:
        service.generate_for_event(_event())

    assert exc_info.value.code == "LLM_INVALID_RESPONSE"


def test_unconfigured_reply_client_does_not_generate_template() -> None:
    client = OpenAICompatibleReplyClient(
        ReplyModelConfig(api_key="", base_url="", model="")
    )

    assert client.status()["configured"] is False
    with pytest.raises(ServiceError) as exc_info:
        client.generate(system_prompt="system", user_prompt="user")

    assert exc_info.value.code == "LLM_NOT_CONFIGURED"


def test_reply_model_config_prefers_saved_values_and_uses_environment_fallback(
    monkeypatch,
) -> None:
    monkeypatch.setenv("XHS_REPLY_LLM_API_KEY", "environment-key")
    monkeypatch.setenv("XHS_REPLY_LLM_MODEL", "environment-model")

    config = ReplyModelConfig.from_sources(
        {
            "base_url": "https://llm.example/v1",
            "model": "saved-model",
            "timeout_seconds": 45,
        }
    )

    assert config.api_key == "environment-key"
    assert config.base_url == "https://llm.example/v1"
    assert config.model == "saved-model"
    assert config.timeout_seconds == 45


def test_reply_client_calls_configured_chat_completion_endpoint() -> None:
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"choices": [{"message": {"content": '{"reply":"你好"}'}}]}

    def request(url, **values):
        calls.append((url, values))
        return FakeResponse()

    client = OpenAICompatibleReplyClient(
        ReplyModelConfig(
            api_key="test-key",
            base_url="https://llm.example/v1",
            model="reply-model",
        ),
        requester=request,
    )

    result = client.generate(system_prompt="system", user_prompt="user")

    assert result == {"content": '{"reply":"你好"}', "model": "reply-model"}
    assert calls[0][0] == "https://llm.example/v1/chat/completions"
    assert calls[0][1]["json"]["messages"][1]["content"] == "user"


def test_application_service_creates_one_review_only_ai_draft(tmp_path) -> None:
    client = FakeReplyClient(
        '{"reply":"可以的，周六下午见～", "intent":"question", '
        '"confidence":0.88, "reason":"笔记已说明活动时间"}'
    )
    intelligence = ReplyIntelligenceService(client)
    store = ProductStore(tmp_path / "product")
    service = ApplicationService(
        product_store=store,
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
        reply_intelligence=intelligence,
    )
    event = service.inbound_events.record(
        account_slot="alpha",
        event_type="note_comment",
        platform_event_id="comment-1",
        occurred_at="2026-08-21T10:00:00+00:00",
        object_type="note",
        object_id="note-1",
        payload=_event()["payload"],
    )["event"]

    first = service.create_intelligent_reply_draft(
        event["event_id"],
        account_slot="alpha",
        verified_uid="owner-1",
    )
    repeated = service.create_intelligent_reply_draft(
        event["event_id"],
        account_slot="alpha",
        verified_uid="owner-1",
    )

    assert first["created"] is True
    assert first["task"]["state"] == "WAITING_APPROVAL"
    assert first["draft"]["status"] == "DRAFT"
    assert first["draft"]["metadata"]["intelligent_reply"]["mode"] == "review_only"
    assert repeated["created"] is False
    assert repeated["draft"]["draft_id"] == first["draft"]["draft_id"]
    assert len(client.calls) == 1


def test_application_service_persists_reply_model_without_returning_key(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.delenv("XHS_REPLY_LLM_API_KEY", raising=False)
    store = ProductStore(tmp_path / "product")
    service = ApplicationService(
        product_store=store,
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
    )

    status = service.update_reply_model_settings(
        confirmed=True,
        api_key="local-test-key",
        base_url="https://llm.example/v1/",
        model="reply-model",
        timeout_seconds=45,
    )

    assert status["configured"] is True
    assert status["configuration_source"] == "webui"
    assert status["api_key_saved"] is True
    assert "local-test-key" not in str(status)
    assert service.reply_intelligence.client.config.api_key == "local-test-key"

    restarted = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        operations_database=OperationsDatabase(path=tmp_path / "operations.db"),
    )
    assert restarted.intelligent_reply_status()["configured"] is True
    assert restarted.reply_intelligence.client.config.model == "reply-model"
