from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from account_memory import AccountMemory
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_worker_service import ManagerWorkerService

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
IDENTITY = {
    "machine_id": "QA001",
    "account_slot": "demo",
    "platform": "xiaohongshu",
    "uid": "demo-uid",
}


def invoke(action: str, root: Path, request: dict) -> dict:
    source = root / "request.json"
    root.mkdir(parents=True, exist_ok=True)
    source.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            str(ROOT / "runtime/run.py"),
            "--action",
            action,
            "--state-root",
            str(root),
            "--input-file",
            str(source),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_delivery_contract_and_source_origin() -> None:
    ManagerWorkerService._validate_function_package_payload(MANIFEST, ROOT)
    for name in MANIFEST["evidence_files"]:
        assert (ROOT / name).is_file()
    assert Path(sys.modules["account_memory"].__file__).resolve().is_relative_to(ROOT)
    assert Path(sys.modules["manager_service"].__file__).resolve().is_relative_to(ROOT)
    assert MANIFEST["task_code"] == "REQ-20260904-001"
    assert MANIFEST["version"] == "1.1"


def test_runtime_inspect_matches_manifest() -> None:
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "runtime/run.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert json.loads(result.stdout)["function_id"] == MANIFEST["function_id"]


def test_executable_feature_entry(tmp_path: Path) -> None:
    function = MANIFEST["function_id"]
    manager = ManagerService(store=ManagerStore(tmp_path))
    manager.store.put("accounts", "QA001:demo", IDENTITY)
    if function == "MEMORY-01":
        row = invoke(
            "save",
            tmp_path,
            {"layer": "shared_core", "content": "表达依据观察", "source": "固定测试"},
        )["memory"]
        invoke(
            "review",
            tmp_path,
            {"memory_id": row["memory_id"], "reviewer": "验收", "decision": "approve"},
        )
        assert len(invoke("search", tmp_path, {"layer": "shared_core"})["memories"]) == 1
    elif function == "REPLY-01":
        row = invoke("save", tmp_path, {**IDENTITY, "reply_length": "两句"})["persona"]
        assert (
            invoke("show", tmp_path, {"persona_id": row["persona_id"]})["persona"]["reply_length"]
            == "两句"
        )
    elif function == "MEMORY-02":
        worker = ManagerWorkerService(root=tmp_path)
        worker.configure({"machine_id": "QA001", "project_version": "1.5.0"})
        worker.accept_task(
            {
                "task_id": "task-a",
                "dispatch_id": "dispatch-a",
                "target_machine_id": "QA001",
                "allowed_versions": ["1.5.0"],
                "task": {
                    "account_slot": "demo",
                    "capability": "search-feeds",
                    "parameters": {"memory_context": {"identity": IDENTITY}},
                },
            }
        )
        invoke(
            "save", tmp_path, {"task_id": "task-a", "layer": "short_term", "content": "继续阅读"}
        )
        assert invoke("recall", tmp_path, {"task_id": "task-a"})["context"]["short_term"]
    else:
        request = {
            "machine_id": "QA001",
            "task_id": "task-a",
            "remote_execution_id": "session-a",
            "memory_identity": IDENTITY,
            "memory_records": [
                {
                    "layer": "runtime_fact",
                    "event_id": "action-1",
                    "content": "SUCCESS",
                }
            ],
        }
        assert invoke("ingest", tmp_path, request)["inserted"] == 1
        assert invoke("ingest", tmp_path, request)["duplicates"] == 1


CASES = json.loads((ROOT / "tests/history_replay.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_history_replay(tmp_path: Path, case: dict) -> None:
    if case["operation"] == "persona":
        manager = ManagerService(store=ManagerStore(tmp_path))
        manager.store.put("accounts", "QA001:demo", IDENTITY)
        persona = manager.save_persona({**IDENTITY, "tone": case["content"]})["persona"]
        request = {
            "task_id": "task-a",
            "machine_id": "QA001",
            "account_slot": "demo",
            "capability": "search-feeds",
            "parameters": {"keyword": "园艺"},
        }
        first = manager._attach_task_context(manager._normalize_task_request(request))
        manager.store.put("tasks", "task-a", {"task_id": "task-a", "request": copy.deepcopy(first)})
        manager.save_persona({**persona, "tone": case["updated_content"]})
        repeated = manager._attach_task_context(manager._normalize_task_request(request))
        assert repeated["persona_snapshot"] == first["persona_snapshot"]
        next_task = manager._attach_task_context(
            manager._normalize_task_request({**request, "task_id": "task-b"})
        )
        assert next_task["persona_snapshot"]["version"] == 2
        assert next_task["persona_snapshot"]["tone"] == case["updated_content"]
        return
    memory = AccountMemory(tmp_path / "memory")
    layer = case["layer"]
    record = {
        "identity": IDENTITY,
        "layer": layer,
        "source": "历史隔离复现",
        "task_id": "task-a",
        "session_id": "session-a",
        "event_id": "event-a",
        "content": case["content"],
    }
    if case["operation"] == "revision":
        inputs = {"task_id": "task-a", "session_id": "session-a", "source": "worker:QA001"}
        memory.intake(IDENTITY, [record], **inputs)
        first = memory.search(identity=IDENTITY, layer=layer, include_inactive=True)["memories"][0]
        if layer == "account_long_term":
            memory.review(first["memory_id"], "approve", "验收")
        memory.intake(
            IDENTITY, [{**record, "version": 2, "content": case["updated_content"]}], **inputs
        )
        assert memory.intake(IDENTITY, [record], **inputs)["duplicates"] == 1
        rows = memory.search(identity=IDENTITY, layer=layer, include_inactive=True)["memories"]
        assert len(rows) == 1
        assert rows[0]["content"] == case["updated_content"]
        assert rows[0]["source_version"] == 2
        assert rows[0]["state"] == ("PENDING_REVIEW" if layer == "account_long_term" else "ACTIVE")
        return
    row = memory.save(record)["memory"]
    if case["operation"] == "review":
        assert not memory.search(identity=IDENTITY, layer=layer)["memories"]
        memory.review(row["memory_id"], "approve", "验收")
        assert len(memory.search(identity=IDENTITY, layer=layer)["memories"]) == 1
    elif case["operation"] == "expiry":
        memory.save({**record, "memory_id": row["memory_id"], "expires_at": case["expires_at"]})
        assert not AccountMemory(tmp_path / "memory").search(identity=IDENTITY, layer=layer)[
            "memories"
        ]
    elif case["operation"] == "identity":
        if layer in {"shared_core", "account_long_term"}:
            memory.review(row["memory_id"], "approve", "验收")
        assert not memory.search(identity={**IDENTITY, "uid": "other-uid"}, layer=layer)["memories"]
    else:
        pytest.fail(f"未实现的回放动作：{case['operation']}")
