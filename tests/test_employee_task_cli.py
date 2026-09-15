from __future__ import annotations

import json
import sys
from pathlib import Path

from employee_task_cli import invoke, write_object
from manager_worker_async import queue_ready

from tests.test_manager_service import register_ready, service, task_request


def test_cli_file_arguments_preserve_text_and_account(tmp_path: Path) -> None:
    write_object(tmp_path / "task.json", {"task_id": "one", "account_slot": "alpha"})
    text = '中文 "引号" & %PATH%\n第二行 ' + "长文本" * 4000
    request = tmp_path / "comment.json"
    write_object(request, {"args": ["post-comment", "--content", text]})
    original = sys.argv

    def cli() -> None:
        assert sys.argv == ["cli", "--account", "alpha", "post-comment", "--content", text]
        print(json.dumps({"content": text}, ensure_ascii=False))
        raise SystemExit(2)

    assert invoke(tmp_path, request, cli) == 2
    assert sys.argv is original
    assert json.loads(request.with_suffix(".output.json").read_text(encoding="utf-8")) == {
        "content": text
    }
    execution = json.loads(request.with_suffix(".execution.json").read_text(encoding="utf-8"))
    assert execution["exit_code"] == 2


def test_queue_is_account_scoped_and_preserves_step_order() -> None:
    def row(key: str, slot: str, state: str = "ACCEPTED") -> dict:
        return {
            "task_id": key,
            "remote_execution_id": key,
            "accepted_at": key,
            "task": {"account_slot": slot},
            "state": state,
        }

    browse = row("1", "alpha")
    comment = row("2", "alpha")
    follow = row("3", "alpha")
    other = row("4", "beta")
    state = {"executions": {r["task_id"]: r for r in [browse, comment, follow, other]}}
    assert queue_ready(state, browse)
    assert queue_ready(state, other)
    assert not queue_ready(state, comment)
    assert not queue_ready(state, follow)
    browse["state"] = "SUCCESS"
    assert queue_ready(state, comment)
    assert not queue_ready(state, follow)
    comment["state"] = "RESULT_UNKNOWN"
    assert queue_ready(state, follow)
    comment["state"] = "SUCCESS"
    assert queue_ready(state, follow)
    follow["task"]["parameters"] = {"depends_on_task_ids": ["missing"]}
    assert not queue_ready(state, follow)


def test_comment_uses_default_persona_when_unconfigured(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    register_ready(manager)
    request = {**task_request(), "capability": "post-comment"}
    manager.dispatch_task(request)
    saved = manager.store.get("tasks", "task-1")["request"]
    assert saved["parameters"]["persona"]["display_name"] == "友好评论者"
    assert manager.dispatch_task(request)["success"]


def test_comment_loads_registered_persona_and_keeps_retry_snapshot(tmp_path: Path) -> None:
    manager, _ = service(tmp_path)
    register_ready(manager)
    manager.store.put(
        "personas",
        "persona-a",
        {
            "persona_id": "persona-a",
            "machine_id": "PC001",
            "account_slot": "brand-a",
            "status": "ACTIVE",
            "tone": "自然简短",
            "version": 1,
        },
    )
    request = {**task_request(), "capability": "post-comment"}
    manager.dispatch_task(request)
    saved = manager.store.get("tasks", "task-1")["request"]
    assert saved["parameters"]["persona"]["tone"] == "自然简短"
    assert manager.dispatch_task(request)["success"]
