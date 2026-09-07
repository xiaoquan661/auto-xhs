from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from service_errors import ServiceError

from scripts.product_store import ProductStore
from scripts.task_service import TaskService


def _service(tmp_path) -> TaskService:
    return TaskService(ProductStore(tmp_path / "product"))


def test_task_creation_uses_policy_and_persists(tmp_path) -> None:
    service = _service(tmp_path)

    task = service.create(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="搜索露营",
    )

    assert task["state"] == "QUEUED"
    assert task["risk_level"] == "L0"
    assert service.get(task["task_id"])["request_summary"] == "搜索露营"


def test_blank_operation_uses_capability_policy(tmp_path) -> None:
    service = _service(tmp_path)

    task = service.create(
        source="manager",
        account_slot="alpha",
        capability="list-feeds",
        operation="",
        request_summary="经理机只读任务",
    )

    assert task["state"] == "QUEUED"
    assert task["risk_level"] == "L0"


def test_task_records_active_or_passive_source_metadata(tmp_path) -> None:
    service = _service(tmp_path)

    active = service.create(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="主动搜索",
    )
    passive = service.create(
        source="platform_event",
        source_event_id="event-1",
        account_slot="alpha",
        capability="reply-comment",
        request_summary="回复新评论",
        target_type="comment",
        target_id="comment-1",
    )

    assert active["source_type"] == "user"
    assert passive["source_type"] == "platform_event"
    assert passive["source_event_id"] == "event-1"
    assert passive["target_type"] == "comment"
    assert passive["target_id"] == "comment-1"


def test_task_keeps_goal_and_persona_snapshots_from_manager(tmp_path) -> None:
    service = _service(tmp_path)

    task = service.create(
        source="manager",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="执行运营目标计划",
        schedule_id="schedule-1",
        operation_task_id="operation-1",
        goal_id="goal-1",
        goal_snapshot={"goal_id": "goal-1", "version": 3, "title": "稳定增长"},
        persona_snapshot={"persona_id": "persona-1", "version": 2},
    )

    assert task["schedule_id"] == "schedule-1"
    assert task["operation_task_id"] == "operation-1"
    assert task["goal_id"] == "goal-1"
    assert task["goal_snapshot"]["version"] == 3
    assert task["persona_snapshot"]["version"] == 2


def test_external_output_waits_for_approval(tmp_path) -> None:
    service = _service(tmp_path)

    task = service.create(
        source="webui",
        account_slot="alpha",
        capability="post-comment",
        request_summary="发表评论草稿",
    )

    assert task["state"] == "WAITING_APPROVAL"


def test_authorized_random_comment_is_immediately_queued(tmp_path) -> None:
    service = _service(tmp_path)

    task = service.create(
        source="webui",
        account_slot="alpha",
        capability="random-comment",
        request_summary="随机评论 1 条",
        parameters={"direct_send_authorized": True, "count": 1},
    )

    assert task["risk_level"] == "L2"
    assert task["state"] == "QUEUED"


def test_disabled_task_is_rejected(tmp_path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ServiceError) as exc_info:
        service.create(
            source="codex",
            account_slot="alpha",
            capability="publish",
            request_summary="发布内容",
        )

    assert exc_info.value.code == "CAPABILITY_DISABLED"


def test_task_transition_has_stable_final_state(tmp_path) -> None:
    service = _service(tmp_path)
    task = service.create(
        source="webui",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="搜索",
    )

    running = service.transition(task["task_id"], "RUNNING")
    completed = service.transition(
        task["task_id"],
        "SUCCESS",
        result_summary="找到 10 条笔记",
    )

    assert running["started_at"]
    assert completed["finished_at"]
    assert completed["result_summary"] == "找到 10 条笔记"
    with pytest.raises(ServiceError, match="不能从 SUCCESS"):
        service.transition(task["task_id"], "RUNNING")


def test_execution_claim_allows_parallel_accounts_but_blocks_same_account(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    first = service.create(
        source="webui",
        account_slot="alpha",
        capability="browse-feeds",
        request_summary="alpha 第一个任务",
        parameters={"duration_minutes": 5, "count": 5},
    )
    duplicate = service.create(
        source="webui",
        account_slot="alpha",
        capability="browse-feeds",
        request_summary="alpha 第二个任务",
        parameters={"duration_minutes": 5, "count": 5},
    )
    other_account = service.create(
        source="webui",
        account_slot="beta",
        capability="browse-feeds",
        request_summary="beta 任务",
        parameters={"duration_minutes": 5, "count": 5},
    )

    assert service.claim_for_execution(first["task_id"])["state"] == "RUNNING"
    blocked = service.claim_for_execution(duplicate["task_id"])
    assert blocked["state"] == "BLOCKED"
    assert blocked["error_code"] == "ACCOUNT_BUSY"
    assert first["task_id"][:8] in blocked["recommended_action"]
    assert service.claim_for_execution(other_account["task_id"])["state"] == "RUNNING"


def test_requeue_clears_previous_attempt_timestamps_and_error(tmp_path) -> None:
    service = _service(tmp_path)
    task = service.create(
        source="webui",
        account_slot="alpha",
        capability="list-feeds",
        request_summary="浏览首页",
    )
    blocked = service.transition(
        task["task_id"],
        "BLOCKED",
        error_code="ACCOUNT_NOT_READY",
        recommended_action="启动 Bridge",
    )

    queued = service.transition(task["task_id"], "QUEUED")

    assert blocked["finished_at"]
    assert queued["started_at"] is None
    assert queued["finished_at"] is None
    assert queued["error_code"] == ""
    assert queued["recommended_action"] == ""


def test_recovery_marks_read_only_failed_and_l1_result_unknown(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    read_task = service.create(
        source="codex",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="搜索",
    )
    like_task = service.create(
        source="codex",
        account_slot="alpha",
        capability="like-feed",
        request_summary="点赞",
    )
    service.transition(read_task["task_id"], "RUNNING")
    service.transition(like_task["task_id"], "RUNNING")

    monkeypatch.setattr("scripts.task_service.process_is_running", lambda pid: False)
    recovered = service.recover_interrupted()
    states = {item["task_id"]: item["state"] for item in recovered}

    assert states[read_task["task_id"]] == "FAILED"
    assert states[like_task["task_id"]] == "RESULT_UNKNOWN"


def test_recovery_marks_never_started_queue_as_blocked(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    task = service.create(
        source="codex",
        account_slot="alpha",
        capability="search-feeds",
        request_summary="搜索",
    )

    monkeypatch.setattr("scripts.task_service.process_is_running", lambda pid: False)
    recovered = service.recover_interrupted()

    assert recovered[0]["task_id"] == task["task_id"]
    assert recovered[0]["state"] == "BLOCKED"
    assert recovered[0]["error_code"] == "SERVICE_INTERRUPTED_BEFORE_START"
    assert "重试或取消" in recovered[0]["recommended_action"]


@pytest.mark.parametrize("finish", [True, False])
def test_new_application_preserves_live_process_tasks_and_recovers_exit(tmp_path, finish) -> None:
    from scripts.application_service import ApplicationService

    scripts_path = Path(__file__).resolve().parents[1] / "scripts"
    code = """
import json, sys
sys.path.insert(0, sys.argv[1])
from product_store import ProductStore
from task_service import TaskService
s = TaskService(ProductStore(sys.argv[2]))
running = s.create(source='codex', account_slot='alpha', capability='like-feed',
                   request_summary='process recovery regression')
s.claim_for_execution(running['task_id'])
queued = s.create(source='codex', account_slot='beta', capability='search-feeds',
                  request_summary='queued process regression')
print(json.dumps([running['task_id'], queued['task_id']]), flush=True)
if sys.stdin.readline().strip() == 'finish':
    s.record_progress(running['task_id'], {'metrics': {'like': {'actual': 1}}})
    s.transition(running['task_id'], 'SUCCESS')
"""
    store = ProductStore(tmp_path / "process-product")
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(scripts_path), str(store.root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        running_id, queued_id = json.loads(process.stdout.readline())
        service = ApplicationService(product_store=store)
        assert service.get_task(running_id)["task"]["state"] == "RUNNING"
        assert service.get_task(queued_id)["task"]["state"] == "QUEUED"
        _, errors = process.communicate("finish\n" if finish else "exit\n", timeout=20)
        assert process.returncode == 0, errors
        restarted = ApplicationService(product_store=store)
        result = restarted.get_task(running_id)["task"]
        assert result["state"] == ("SUCCESS" if finish else "RESULT_UNKNOWN")
        if finish:
            assert result["progress_events"][0]["metrics"]["like"]["actual"] == 1
        assert restarted.get_task(queued_id)["task"]["state"] == "BLOCKED"
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


def test_legacy_running_task_preserves_live_account_lock(tmp_path, monkeypatch) -> None:
    from scripts.run_lock import RunLock

    service = _service(tmp_path)
    task = service.create(
        source="codex", account_slot="alpha", capability="like-feed", request_summary="旧任务恢复"
    )
    service.claim_for_execution(task["task_id"])
    service.store.update(
        "tasks",
        task["task_id"],
        lambda current: {k: v for k, v in current.items() if k != "owner_pid"},
    )
    lock = RunLock(str(tmp_path / "account.lock"))
    monkeypatch.setattr("scripts.task_service.for_account", lambda account: lock)
    with lock:
        assert service.recover_interrupted() == []
    assert service.recover_interrupted()[0]["state"] == "RESULT_UNKNOWN"
