from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from account_memory import AccountMemory
from application_service import ApplicationService
from manager_agent_service import ManagerAgentService
from manager_application_memory import ManagerReplyMemoryResolver
from manager_service import ManagerService
from manager_store import ManagerStore
from memory_context import ORDER, source_refs
from memory_review_service import MemoryReviewService
from product_store import ProductStore
from reply_intelligence_service import ReplyIntelligenceService
from service_errors import ServiceError

from tests.test_account_memory import IDENTITY, manager, request, worker, write


class ReviewClient:
    def __init__(self, **verdict: object) -> None:
        self.verdict = {"decision": "approve", "reason": "经验与回读证据一致", **verdict}
        self.inputs: list[dict] = []

    def status(self) -> dict:
        return {"configured": True}

    def generate(self, **prompts: str) -> dict:
        self.inputs.append(json.loads(prompts["user_prompt"]))
        return {"content": json.dumps(self.verdict, ensure_ascii=False), "model": "test-model"}


class ContentClient:
    def __init__(self) -> None:
        self.inputs: list[dict] = []

    def generate(self, **prompts: str) -> dict:
        context = (
            json.loads(prompts["user_prompt"].split("\n", 1)[-1])
            if prompts["user_prompt"].startswith("下面")
            else json.loads(prompts["user_prompt"])
        )
        memory = context.get("memory", context)
        self.inputs.append(memory)
        rows = memory["account_long_term"]
        text = rows[0]["content"] if rows else "请补充实际环境"
        return {
            "model": "test-model",
            "content": json.dumps(
                {
                    "reply": text,
                    "comment": text,
                    "intent": "question",
                    "confidence": 0.9,
                    "adopted_memory_refs": [rows[0]["ref"]] if rows else [],
                },
                ensure_ascii=False,
            ),
        }


def test_five_review_states_and_protected_authority(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    first = write(memory, content="先看光照", canonical_key="light")
    client = ReviewClient()
    assert MemoryReviewService(memory, client).run()["reviews"][0]["status"] == "APPROVED"
    assert memory.get_memory(first["memory_id"])["imported_at"]
    repeated = write(memory, content="先看光照")
    conflict = write(memory, content="无需看光照", canonical_key="light")
    MemoryReviewService(memory, client).run()
    assert memory.get_memory(repeated["memory_id"])["state"] == "DUPLICATE"
    assert memory.get_memory(conflict["memory_id"])["state"] == "CONFLICT"
    rejected = write(memory, content="无依据的说法")
    MemoryReviewService(memory, ReviewClient(decision="reject")).run()
    assert memory.get_memory(rejected["memory_id"])["state"] == "REJECTED"
    core = write(memory, "shared_core", category="governance", content="遵守运营边界")
    assert MemoryReviewService(memory, client).run()["status"] == "IDLE"
    assert memory.get_memory(core["memory_id"])["review_status"] == "PENDING_REVIEW"
    with pytest.raises(ServiceError, match="用户确认"):
        memory.review(core["memory_id"], "approve", "经理 Agent", actor="manager_agent")
    memory.review(core["memory_id"], "approve", "用户")
    loaded = memory.recall(IDENTITY)
    assert loaded["order"] == ORDER
    assert loaded["governance"][0]["memory_id"] == core["memory_id"]
    assert not loaded["shared_core"]


def test_semantic_core_escalation_and_pending_evidence(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    core = write(memory, content="建议修改账号人格")
    MemoryReviewService(memory, ReviewClient(requires_human=True)).run()
    assert memory.get_memory(core["memory_id"])["review_authority"] == "human"
    pending = write(memory, content="需要补充依据")
    client = ReviewClient(decision="pending")
    MemoryReviewService(memory, client).run()
    MemoryReviewService(memory, client).run()
    assert len(client.inputs) == 1
    write(memory, memory_id=pending["memory_id"], content="补充后的经验")
    MemoryReviewService(memory, ReviewClient()).run()
    assert memory.get_memory(pending["memory_id"])["state"] == "ACTIVE"


def test_model_unavailable_leaves_pending(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    row = write(memory)
    client = SimpleNamespace(status=lambda: {"configured": False})
    assert MemoryReviewService(memory, client).run()["status"] == "WAITING_MODEL"
    assert memory.get_memory(row["memory_id"])["state"] == "PENDING_REVIEW"


def test_review_rejects_changed_version_and_cross_account_link(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    first = write(memory)
    updated = write(memory, memory_id=first["memory_id"], content="新版经验")
    with pytest.raises(ServiceError, match="重新审核"):
        memory.review(first["memory_id"], "approve", "经理", expected_version=first["version"])
    foreign = write(memory, identity={**IDENTITY, "uid": "other"})
    memory.review(foreign["memory_id"], "approve", "用户")
    with pytest.raises(ServiceError, match="同一记忆范围"):
        memory.review(updated["memory_id"], "duplicate", "经理", related_id=foreign["memory_id"])


def test_correction_replacement_expiry_history(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    row = write(memory)
    MemoryReviewService(memory, ReviewClient()).run()
    for change, content in (("correct", "核对光照"), ("replace", "核对通风")):
        memory.update_memory(
            {
                "memory_id": row["memory_id"],
                "change": change,
                "content": content,
                "reason": "新任务回读",
            }
        )
        assert not memory.recall(IDENTITY)["account_long_term"]
        MemoryReviewService(memory, ReviewClient()).run()
        assert memory.recall(IDENTITY)["account_long_term"][0]["content"] == content
    memory.update_memory(
        {"memory_id": row["memory_id"], "change": "expire", "reason": "适用期结束"}
    )
    assert not AccountMemory(tmp_path).recall(IDENTITY)["account_long_term"]
    history = memory.history(row["memory_id"])["versions"]
    assert history[-1]["state"] == "EXPIRED"
    assert history[0]["valid_until"]
    assert {r["change"] for r in history} >= {"correct", "replace", "expire"}


def test_review_to_next_task_comment_and_returned_usage(tmp_path: Path) -> None:
    service = manager(tmp_path / "manager")
    persona = service.save_persona(
        {"machine_id": "PC001", "account_slot": "alpha", "tone": "自然具体"}
    )["persona"]
    records = [{"event_id": "experience-a", "layer": "account_long_term", "content": "先核对光照"}]
    result = {
        "machine_id": "PC001",
        "task_id": "prior-task",
        "remote_execution_id": "prior-session",
        "memory_identity": IDENTITY,
        "memory_records": records,
    }
    assert service.ingest_memory(result)["inserted"] == 1
    assert service.ingest_memory(result)["duplicates"] == 1
    MemoryReviewService(service.memory, ReviewClient()).run()
    task = service._attach_task_context(service._normalize_task_request(request()))
    employee = worker(tmp_path / "worker", task)
    outcomes = []

    def execute(payload: dict) -> dict:
        context = payload["parameters"]["memory_context"]
        assert context["persona"]["tone"] == "自然具体"
        usage = employee.memory.record_usage(
            context, task_id=payload["task_id"], stage="comment_generation",
            adopted=[row["ref"] for row in source_refs(context)], outcome="COMMENT_GENERATED",
        )
        outcomes.append(usage)
        return {
            "state": "SUCCESS",
            "business_result": {"adopted_memory_refs": usage["adopted_refs"]},
        }

    employee.run_task("task-a", executor=execute)
    assert outcomes[0]["adopted_refs"]
    assert outcomes[0]["persona_version"] == persona["version"]
    payload = employee.result_memory(
        employee._find_execution(employee._store.snapshot(), "task-a", None)
    )
    service.ingest_memory(
        {
            **payload,
            "machine_id": "PC001",
            "task_id": "task-a",
            "remote_execution_id": "new-session",
        }
    )
    uses = service.memory.usages(IDENTITY, "task-a")["usages"]
    assert any(u["stage"] == "comment_generation" and u["adopted_refs"] for u in uses)
    assert not service.memory.usages({**IDENTITY, "uid": "other"})["usages"]


def test_reply_generation_uses_updated_manager_knowledge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XHS_MANAGER_HOME", str(tmp_path / "manager"))
    monkeypatch.setenv("XHS_MANAGER_WORKER_HOME", str(tmp_path / "worker"))
    memory = AccountMemory(tmp_path / "manager/memory")
    row = write(memory, content="先核对光照")
    MemoryReviewService(memory, ReviewClient()).run()
    client = ContentClient()
    app = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        memory_context_resolver=ManagerReplyMemoryResolver(ManagerStore(tmp_path / "manager")),
        reply_intelligence=ReplyIntelligenceService(client),
    )
    event = {
        "event_id": "reply-a",
        "event_type": "note_comment",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "payload": {"content": "怎么养盆栽", "note_title": "盆栽"},
    }
    first = app.generate_reply_with_memory(event, "uid-a")
    assert first["reply"] == "先核对光照"
    memory.update_memory(
        {
            "memory_id": row["memory_id"],
            "change": "replace",
            "reason": "回读更新",
            "content": "先核对通风",
        }
    )
    MemoryReviewService(memory, ReviewClient()).run()
    second = app.generate_reply_with_memory({**event, "event_id": "reply-b"}, "uid-a")
    assert second["reply"] == "先核对通风"
    assert first["adopted_memory_refs"] != second["adopted_memory_refs"]
    assert second["memory_usage"]["adoption_reported"]
    foreign = app.generate_reply_with_memory(event, "uid-b")
    assert not foreign["memory_sources"]
    assert list(client.inputs[0]) == ORDER


def test_manager_agent_cycle_runs_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = ManagerService(store=ManagerStore(tmp_path))
    row = write(service.memory)
    client = ReviewClient()
    monkeypatch.setattr("memory_review_service.OpenAICompatibleReplyClient", lambda config: client)
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(tmp_path / "product"))
    result = ManagerAgentService(service).run_cycle()
    assert result["memory_review"]["reviews"][0]["status"] == "APPROVED"
    assert service.memory.get_memory(row["memory_id"])["review_actor"] == "manager_agent"


def test_expired_frozen_knowledge_and_cross_day_handoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = manager(tmp_path / "manager")
    write(service.memory, expires_at="2026-09-10T00:00:00+00:00")
    monkeypatch.setattr("account_memory.now", lambda: "2026-09-09T00:00:00+00:00")
    MemoryReviewService(service.memory, ReviewClient()).run()
    task = service._attach_task_context(service._normalize_task_request(request()))
    employee = worker(tmp_path / "worker", task)
    write(
        employee.memory, "handoff", expires_at="2026-09-12T00:00:00+00:00", content="继续核对通风"
    )
    monkeypatch.setattr("account_memory.now", lambda: "2026-09-11T00:00:00+00:00")
    execution = employee._find_execution(employee._store.snapshot(), "task-a", None)
    restored = employee.load_memory(execution)
    assert not restored["account_long_term"]
    assert restored["handoff"][0]["content"] == "继续核对通风"
    assert source_refs(restored)


def test_employee_reply_uses_received_context_with_empty_manager_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = manager(tmp_path / "source")
    write(source.memory, content="按实际光照调整养护")
    MemoryReviewService(source.memory, ReviewClient()).run()
    task = source._attach_task_context(source._normalize_task_request(request()))
    employee = worker(tmp_path / "worker", task)
    execution = employee._find_execution(employee._store.snapshot(), "task-a", None)
    employee.load_memory(execution)
    monkeypatch.setenv("XHS_MANAGER_HOME", str(tmp_path / "empty-manager"))
    monkeypatch.setenv("XHS_MANAGER_WORKER_HOME", str(tmp_path / "worker"))
    AccountMemory(tmp_path / "empty-manager/memory").pending_reviews()
    app = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        reply_intelligence=ReplyIntelligenceService(ContentClient()),
    )
    event = {
        "event_id": "event-a",
        "event_type": "note_comment",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "payload": {"content": "如何养护"},
    }
    generated = app.generate_reply_with_memory(event, "uid-a")
    assert generated["reply"] == "按实际光照调整养护"
    assert employee.memory.usages(IDENTITY, "task-a")["usages"][0]["adopted_refs"]


def test_approved_shared_admission_preserves_origin(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    original = write(memory, content="评论依据笔记正文")
    MemoryReviewService(memory, ReviewClient(target_layer="shared_core")).run()
    reviewed = memory.get_memory(original["memory_id"])
    assert reviewed["state"] == "PROMOTED"
    assert reviewed["review_status"] == "APPROVED"
    shared = memory.recall({**IDENTITY, "uid": "other"})["shared_core"][0]
    assert shared["origin_memory_id"] == original["memory_id"]
    assert shared["origin_identity"] == IDENTITY
    assert not memory.recall(IDENTITY)["account_long_term"]



def test_employee_reply_keeps_received_context_when_manager_store_is_available(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = manager(tmp_path / "manager")
    row = write(source.memory, content="沿已下发任务核对光照")
    MemoryReviewService(source.memory, ReviewClient()).run()
    task = source._attach_task_context(source._normalize_task_request(request()))
    employee = worker(tmp_path / "worker", task)
    execution = employee._find_execution(employee._store.snapshot(), "task-a", None)
    employee.load_memory(execution)
    source.memory.update_memory(
        {
            "memory_id": row["memory_id"],
            "change": "replace",
            "reason": "经理更新知识",
            "content": "经理当前要求核对通风",
        }
    )
    MemoryReviewService(source.memory, ReviewClient()).run()
    monkeypatch.setenv("XHS_MANAGER_HOME", str(tmp_path / "manager"))
    monkeypatch.setenv("XHS_MANAGER_WORKER_HOME", str(tmp_path / "worker"))
    event = {
        "event_id": "employee-reply-a",
        "event_type": "note_comment",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "payload": {"content": "如何养护"},
    }
    employee_app = ApplicationService(
        product_store=ProductStore(tmp_path / "employee-product"),
        reply_intelligence=ReplyIntelligenceService(ContentClient()),
    )
    manager_app = ApplicationService(
        product_store=ProductStore(tmp_path / "manager-product"),
        reply_intelligence=ReplyIntelligenceService(ContentClient()),
        memory_context_resolver=ManagerReplyMemoryResolver(source.store),
    )
    assert employee_app.generate_reply_with_memory(event, "uid-a")["reply"] == (
        "沿已下发任务核对光照"
    )
    assert manager_app.generate_reply_with_memory(event, "uid-a")["reply"] == (
        "经理当前要求核对通风"
    )
