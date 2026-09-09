from __future__ import annotations

import copy
import json
import sqlite3
from pathlib import Path

import pytest
from account_memory import AccountMemory
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_worker import dispatch_request
from manager_worker_service import ManagerWorkerService
from service_errors import ServiceError
from web_server import _dispatch_mutation

IDENTITY = {
    "machine_id": "PC001",
    "account_slot": "alpha",
    "platform": "xiaohongshu",
    "uid": "uid-a",
}


def write(memory: AccountMemory, layer: str = "account_long_term", **changes: object) -> dict:
    return memory.save(
        {
            "identity": IDENTITY,
            "layer": layer,
            "source": "验收样例",
            "content": "园艺账号分享盆栽养护经验",
            "task_id": "task-a",
            "session_id": "session-a",
            **changes,
        }
    )["memory"]


def test_review_scope_version_and_retirement(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    row = write(memory)
    assert not memory.search(identity=IDENTITY, layer="account_long_term")["memories"]
    memory.review(row["memory_id"], "approve", "经理")
    assert memory.search(identity=IDENTITY, layer="account_long_term", query="盆栽")["memories"]
    assert not memory.search(identity={**IDENTITY, "uid": "uid-b"}, layer="account_long_term")[
        "memories"
    ]
    assert not memory.search(
        identity={**IDENTITY, "platform": "rednote"}, layer="account_long_term"
    )["memories"]
    revised = write(memory, memory_id=row["memory_id"], content="优先询问光照条件")
    assert revised["state"] == "PENDING_REVIEW"
    assert not memory.search(identity=IDENTITY, layer="account_long_term")["memories"]
    memory.review(row["memory_id"], "approve", "经理")
    memory.review(row["memory_id"], "retire", "经理")
    assert not memory.search(identity=IDENTITY, layer="account_long_term")["memories"]
    history = memory.history(row["memory_id"])["versions"]
    assert len(history) == 5
    assert history[1]["reviewed_by"] == "经理"
    assert history[1]["effective_at"]


def test_shared_core_and_unknown_identity(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    row = write(memory, "shared_core")
    memory.review(row["memory_id"], "approve", "经理")
    assert memory.recall({**IDENTITY, "uid": "uid-b"})["shared_core"]
    with pytest.raises(ServiceError, match="核验账号 UID"):
        memory.search(identity={"account_slot": "alpha"}, layer="account_long_term")
    with pytest.raises(ServiceError, match="身份和层次不能变更"):
        write(memory, "account_long_term", memory_id=row["memory_id"])


def test_short_term_restart_task_scope_and_expiry(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    row = write(memory, "short_term", summary="下次核对光照")
    write(memory, "short_term", task_id="task-b")
    write(memory, "handoff", summary="会话交接")
    reopened = AccountMemory(tmp_path)
    restored = reopened.recall(IDENTITY, task_id="task-a", session_id="session-a")
    assert [r["memory_id"] for r in restored["short_term"]] == [row["memory_id"]]
    assert len(restored["handoff"]) == 1
    assert not reopened.recall({**IDENTITY, "uid": "uid-b"})["short_term"]
    with reopened.connect() as connection:
        connection.execute(
            "UPDATE memories SET expires_at=? WHERE memory_id=?",
            ("2000-01-01T00:00:00+00:00", row["memory_id"]),
        )
    assert not reopened.search(identity=IDENTITY, layer="short_term", task_id="task-a")["memories"]
    assert reopened.search(
        identity=IDENTITY, layer="short_term", task_id="task-a", include_inactive=True
    )["memories"]


def test_intake_replay_separates_facts_and_candidates(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    rows = [
        {"layer": "runtime_fact", "event_id": "action-1", "content": "RESULT_UNKNOWN"},
        {"layer": "account_long_term", "event_id": "learning-1", "content": "专业经验建议"},
    ]

    def intake() -> dict:
        return memory.intake(
            IDENTITY, rows, task_id="task-a", session_id="session-a", source="PC001"
        )

    assert intake()["inserted"] == 2
    assert intake()["duplicates"] == 2
    assert len(memory.search(identity=IDENTITY, layer="runtime_fact")["memories"]) == 1
    assert not memory.search(identity=IDENTITY, layer="account_long_term")["memories"]


def manager(root: Path) -> ManagerService:
    service = ManagerService(store=ManagerStore(root))
    service.store.put("accounts", "PC001:alpha", {**IDENTITY, "account_key": "PC001:alpha"})
    return service


def request() -> dict:
    return {
        "task_id": "task-a",
        "machine_id": "PC001",
        "account_slot": "alpha",
        "capability": "search-feeds",
        "parameters": {"keyword": "园艺"},
    }


def test_persona_task_snapshot_and_identity_change(tmp_path: Path) -> None:
    service = manager(tmp_path)
    persona = service.save_persona(
        {"machine_id": "PC001", "account_slot": "alpha", "tone": "简洁", "reply_length": "两句"}
    )["persona"]
    assert persona["memory_namespace"]
    first = service._attach_task_context(service._normalize_task_request(request()))
    assert first["persona_snapshot"]["reply_length"] == "两句"
    service.store.put("tasks", "task-a", {"task_id": "task-a", "request": copy.deepcopy(first)})
    service.save_persona({**persona, "tone": "亲切"})
    repeated = service._attach_task_context(service._normalize_task_request(request()))
    assert repeated == first
    next_task = service._attach_task_context(
        service._normalize_task_request({**request(), "task_id": "task-b"})
    )
    assert next_task["persona_snapshot"]["version"] == 2
    service.store.put("accounts", "PC001:alpha", {**IDENTITY, "uid": "new-uid"})
    with pytest.raises(ServiceError, match="更新账号人格"):
        service._attach_task_context(
            service._normalize_task_request({**request(), "task_id": "task-c"})
        )
    assert service._attach_task_context(service._normalize_task_request(request())) == first


def worker(root: Path, task: dict) -> ManagerWorkerService:
    service = ManagerWorkerService(root=root)
    service.configure({"machine_id": "PC001", "project_version": "1.5.0"})
    service.accept_task(
        {
            "task_id": "task-a",
            "dispatch_id": "dispatch-a",
            "target_machine_id": "PC001",
            "allowed_versions": ["1.5.0"],
            "task": task,
        }
    )
    return service


def test_worker_recovery_return_and_manager_replay(tmp_path: Path) -> None:
    mgr = manager(tmp_path / "manager")
    task = mgr._attach_task_context(mgr._normalize_task_request(request()))
    mgr.store.put("tasks", "task-a", {"task_id": "task-a", "machine_id": "PC001", "request": task})
    employee = worker(tmp_path / "worker", task)
    dispatch_request(
        employee,
        {
            "action": "memory-save",
            "task_id": "task-a",
            "layer": "short_term",
            "content": "已读完前两篇",
            "summary": "接着读第三篇",
        },
    )
    employee = ManagerWorkerService(root=employee.root)
    context = dispatch_request(employee, {"action": "memory-recall", "task_id": "task-a"})[
        "context"
    ]
    assert context["short_term"][0]["summary"] == "接着读第三篇"
    dispatch_request(
        employee,
        {
            "action": "memory-save",
            "task_id": "task-a",
            "layer": "account_long_term",
            "content": "读者更关心日照条件",
        },
    )
    state = employee._store.snapshot()
    events = list((Path(state["config"]["task_outbox_root"]) / "events").glob("*.json"))
    assert events
    for path in events:
        body = json.loads(path.read_text(encoding="utf-8"))
        mgr.record_agent_message(body["payload"]["agent_message"])
        mgr.record_agent_message(body["payload"]["agent_message"])
    assert (
        len(
            mgr.memory.search(identity=IDENTITY, layer="account_long_term", include_inactive=True)[
                "memories"
            ]
        )
        == 1
    )
    seen = []
    employee.run_task(
        "task-a",
        executor=lambda payload: (
            seen.append(payload)
            or {
                "state": "SUCCESS",
                "business_result": {"success": True},
                "message": "已读完",
            }
        ),
    )
    assert seen[0]["parameters"]["memory_context"]["short_term"]
    result = employee._store.snapshot()["results"][-1]
    result["machine_id"] = "PC001"
    imported = mgr.ingest_memory(result)
    assert imported["inserted"] == 2
    assert mgr.ingest_memory(result)["inserted"] == 0
    assert len(mgr.memory.search(identity=IDENTITY, layer="handoff")["memories"]) == 1
    assert not mgr.memory.search(identity=IDENTITY, layer="account_long_term")["memories"]


def test_memory_http_api(tmp_path: Path) -> None:
    service = manager(tmp_path)

    def api(action: str, body: dict) -> dict:
        return _dispatch_mutation(
            None, "POST", f"/api/v1/manager/memory/{action}", body, manager_service=service
        )

    saved = api(
        "save",
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "layer": "shared_core",
            "source": "品牌规则",
            "content": "表达自然，有事实依据",
        },
    )["memory"]
    api("review", {"memory_id": saved["memory_id"], "decision": "approve", "reviewer": "经理"})
    assert len(api("search", {"layer": "shared_core"})["memories"]) == 1
    assert len(api("history", {"memory_id": saved["memory_id"]})["versions"]) == 2


def test_delayed_handoff_keeps_original_expiry(tmp_path: Path) -> None:
    memory = AccountMemory(tmp_path)
    row = {
        "layer": "handoff",
        "event_id": "old-summary",
        "content": "旧交接",
        "expires_at": "2000-01-01T00:00:00+00:00",
    }
    memory.intake(IDENTITY, [row], task_id="task-a", session_id="session-a", source="PC001")
    assert not memory.search(identity=IDENTITY, layer="handoff")["memories"]


def test_dispatch_retry_preserves_persona_and_memory(tmp_path: Path) -> None:
    service = manager(tmp_path)
    service.store.put("machines", "PC001", {"machine_id": "PC001", "desired_paused": True})
    persona = service.save_persona({"machine_id": "PC001", "account_slot": "alpha"})["persona"]
    first = service.dispatch_task(request())["task"]["request"]
    service.save_persona({**persona, "tone": "新语气"})
    repeated = service.dispatch_task(request())["task"]["request"]
    assert first == repeated


def test_local_handoff_is_not_duplicated_on_final_return(tmp_path: Path) -> None:
    mgr = manager(tmp_path / "manager")
    task = mgr._attach_task_context(mgr._normalize_task_request(request()))
    employee = worker(tmp_path / "worker", task)
    dispatch_request(
        employee,
        {
            "action": "memory-save",
            "task_id": "task-a",
            "layer": "handoff",
            "event_id": "summary-1",
            "content": "接着阅读第三篇",
        },
    )
    employee.run_task("task-a", executor=lambda payload: {"state": "SUCCESS", "result": {}})
    rows = employee.memory.search(identity=IDENTITY, layer="handoff")["memories"]
    assert sum(row["content"] == "接着阅读第三篇" for row in rows) == 1
    assert len(rows) == 2


def test_memory_storage_error_preserves_business_result(tmp_path: Path, monkeypatch) -> None:
    mgr = manager(tmp_path / "manager")
    task = mgr._attach_task_context(mgr._normalize_task_request(request()))
    employee = worker(tmp_path / "worker", task)

    def fail(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("记忆库暂时不可写")

    monkeypatch.setattr(AccountMemory, "intake", fail)
    employee.run_task(
        "task-a", executor=lambda payload: {"state": "SUCCESS", "result": {"count": 3}}
    )
    result = employee._store.snapshot()["results"][-1]
    assert result["state"] == "SUCCESS"
    assert result["business_result"]["count"] == 3
    assert result["memory_status"] == "PENDING_REPAIR"


def test_live_identity_change_blocks_frozen_context(tmp_path: Path, monkeypatch) -> None:
    from application_service import ApplicationService
    from product_store import ProductStore

    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))
    monkeypatch.setattr(
        service,
        "get_account_status",
        lambda account: {
            "ready": True,
            "identity": {"live_user_id": "new-uid"},
        },
    )
    task = service.create_task(
        source="manager",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="测试记忆身份",
        parameters={
            "keyword": "盆栽",
            "memory_context": {"identity": IDENTITY},
        },
    )["task"]
    result = service.execute_task(task["task_id"])["task"]
    assert result["state"] == "BLOCKED"
    assert result["error_code"] == "MEMORY_IDENTITY_MISMATCH"


def test_worker_refreshes_expiration_of_loaded_short_term(tmp_path: Path) -> None:
    employee = ManagerWorkerService(root=tmp_path)
    execution = {
        "task_id": "task-a",
        "remote_execution_id": "session-a",
        "task": {"parameters": {"memory_context": {"identity": IDENTITY}}},
    }
    row = write(employee.memory, "short_term")
    execution["task"]["parameters"]["memory_context"] = employee.load_memory(execution)
    write(
        employee.memory,
        "short_term",
        memory_id=row["memory_id"],
        expires_at="2000-01-01T00:00:00+00:00",
    )
    assert not employee.load_memory(execution)["short_term"]


@pytest.mark.parametrize("layer", ["handoff", "account_long_term"])
def test_intake_applies_new_source_revision_and_ignores_old_replay(
    tmp_path: Path, layer: str
) -> None:
    memory = AccountMemory(tmp_path)
    args = {"task_id": "task-a", "session_id": "session-a", "source": "worker:PC001"}
    record = {"event_id": "record-a", "layer": layer, "content": "初次记录", "version": 1}
    memory.intake(IDENTITY, [record], **args)
    first = memory.search(identity=IDENTITY, layer=layer, include_inactive=True)["memories"][0]
    if layer == "account_long_term":
        memory.review(first["memory_id"], "approve", "经理")
    memory.intake(IDENTITY, [{**record, "content": "更新后的记录", "version": 2}], **args)
    assert memory.intake(IDENTITY, [record], **args)["duplicates"] == 1
    rows = memory.search(identity=IDENTITY, layer=layer, include_inactive=True)["memories"]
    assert len(rows) == 1
    assert rows[0]["memory_id"] == first["memory_id"]
    assert rows[0]["content"] == "更新后的记录"
    assert rows[0]["source_version"] == 2
    assert rows[0]["state"] == ("PENDING_REVIEW" if layer == "account_long_term" else "ACTIVE")


def test_task_recall_prioritizes_older_relevant_long_term(tmp_path: Path) -> None:
    service = manager(tmp_path)
    first = write(service.memory, content="园艺笔记：先查看光照")
    service.memory.review(first["memory_id"], "approve", "经理")
    for i in range(30):
        row = write(service.memory, content=f"其他知识 {i}")
        service.memory.review(row["memory_id"], "approve", "经理")
    task = service._attach_task_context(service._normalize_task_request(request()))
    rows = task["parameters"]["memory_context"]["account_long_term"]
    assert len(rows) == 30
    assert rows[0]["memory_id"] == first["memory_id"]


def test_worker_excludes_other_task_short_term_snapshot(tmp_path: Path) -> None:
    employee = ManagerWorkerService(root=tmp_path)
    foreign = write(employee.memory, "short_term", task_id="task-b")
    execution = {
        "task_id": "task-a",
        "remote_execution_id": "session-a",
        "task": {
            "parameters": {"memory_context": {"identity": IDENTITY, "short_term": [foreign]}},
        },
    }
    assert not employee.load_memory(execution)["short_term"]
