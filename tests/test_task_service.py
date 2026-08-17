from __future__ import annotations

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


def test_recovery_marks_read_only_failed_and_l1_result_unknown(tmp_path) -> None:
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

    recovered = service.recover_interrupted()
    states = {item["task_id"]: item["state"] for item in recovered}

    assert states[read_task["task_id"]] == "FAILED"
    assert states[like_task["task_id"]] == "RESULT_UNKNOWN"
