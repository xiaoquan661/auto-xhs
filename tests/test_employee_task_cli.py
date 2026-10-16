from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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
            "accepted_at": f"2026-09-11T00:00:0{key}+00:00",
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


def test_business_task_keeps_identity_checks_out_of_browser(tmp_path: Path) -> None:
    from employee_recovery import recovery_for

    write_object(
        tmp_path / "task.json", {"task_id": "one", "account_slot": "alpha", "capability": "publish"}
    )
    for command in ("check-login", "account-identity"):
        request = tmp_path / f"{command}.json"
        write_object(request, {"args": [command]})
        called = []
        assert invoke(tmp_path, request, lambda called=called: called.append(True)) == 2
        assert not called
    recovery = recovery_for({"error_code": "LOGIN_REQUIRED"}, 1)
    assert recovery["user_action_required"] is True
    assert recovery["identity_check_required"] is False


def test_explicit_identity_task_remains_available(tmp_path: Path) -> None:
    write_object(
        tmp_path / "task.json",
        {"task_id": "one", "account_slot": "alpha", "capability": "account-identity"},
    )
    request = tmp_path / "identity.json"
    write_object(request, {"args": ["account-identity"]})
    called = []
    assert invoke(tmp_path, request, lambda called=called: called.append(True)) == 0
    assert called == [True]


def test_home_exclusions_injected_and_completed_step_not_replayed(tmp_path):
    write_object(
        tmp_path / "task.json",
        {
            "task_id": "one",
            "account_slot": "alpha",
            "parameters": {"excluded_feed_ids": ["note-a", "note-b"]},
        },
    )
    request = tmp_path / "browse.json"
    write_object(request, {"args": ["home-engagement", "--browse-count", "15"]})
    calls = []

    def cli():
        calls.append(list(sys.argv))
        assert sys.argv[-4:] == ["--exclude-feed-id", "note-a", "--exclude-feed-id", "note-b"]
        print('{"success": true}')

    assert invoke(tmp_path, request, cli) == 0
    assert invoke(tmp_path, request, cli) == 0
    assert len(calls) == 1


@pytest.mark.parametrize(
    "browsed,liked,unknown,retried",
    [(0, 0, False, True), (3, 1, False, False), (0, 0, True, False)],
)
def test_interrupted_engagement_recovery_preserves_action_evidence(
    tmp_path: Path, browsed: int, liked: int, unknown: bool, retried: bool
) -> None:
    write_object(tmp_path / "task.json", {"task_id": "one", "account_slot": "alpha"})
    request = tmp_path / "engagement.json"
    write_object(request, {"args": ["home-engagement", "--browse-count", "10"]})
    payload = {
        "success": False,
        "result_type": "home_engagement",
        "stop_reason": "page_interrupted",
        "result_unknown": unknown,
        "counts": {"browsed": browsed, "liked": liked},
        "items": [{"feed_id": "done"}] if browsed else [],
    }
    calls = []

    def cli() -> None:
        calls.append(True)
        print(json.dumps(payload if len(calls) == 1 else {"success": True}))
        raise SystemExit(2 if len(calls) == 1 else 0)

    assert invoke(tmp_path, request, cli) == 2
    recovery = json.loads(request.with_suffix(".recovery.json").read_text(encoding="utf-8"))
    assert recovery["kind"] == ("reconcile" if unknown else "observe")
    assert invoke(tmp_path, request, cli) == (0 if retried else 2)
    assert len(calls) == (2 if retried else 1)
    if retried:
        archived = list((tmp_path / "step-attempts").glob("*/engagement.output.json"))
        assert len(archived) == 1
        assert json.loads(archived[0].read_text(encoding="utf-8")) == payload
        assert invoke(tmp_path, request, cli) == 0
        assert len(calls) == 2
