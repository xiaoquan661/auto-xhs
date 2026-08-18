from __future__ import annotations

import pytest

from product_store import ProductStore
from publish_workflow import PublishWorkflowService
from service_errors import ServiceError


def _service(tmp_path) -> PublishWorkflowService:
    return PublishWorkflowService(ProductStore(tmp_path / "product"))


def _preview() -> dict:
    return {
        "kind": "image",
        "title": "测试标题",
        "content": "测试正文",
        "assets": [r"C:\Media\one.jpg"],
        "asset_count": 1,
        "schedule_at": None,
        "visibility": "公开可见",
    }


def _waiting_task(service: PublishWorkflowService) -> dict:
    task = service.begin(
        account="alpha",
        capability="fill-publish",
        request_summary="图文发布预览：测试标题",
        preview=_preview(),
    )
    assert task["state"] == "RUNNING"
    return service.wait_for_confirmation(
        task["task_id"],
        summary="图文已填写到浏览器，等待用户确认真实预览",
    )


def test_publish_workflow_persists_preview_for_webui_monitoring(tmp_path) -> None:
    service = _service(tmp_path)

    task = _waiting_task(service)
    persisted = service.tasks.get(task["task_id"])

    assert persisted["source"] == "agent"
    assert persisted["state"] == "WAITING_APPROVAL"
    assert persisted["parameters"]["stage"] == "preview_ready"
    assert persisted["parameters"]["preview"]["title"] == "测试标题"


def test_publish_requires_confirmation_and_matching_account(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)

    with pytest.raises(ServiceError) as missing:
        service.prepare_publish(task["task_id"], account="alpha", confirmed=False)
    with pytest.raises(ServiceError) as mismatch:
        service.prepare_publish(task["task_id"], account="beta", confirmed=True)

    assert missing.value.code == "CONFIRMATION_REQUIRED"
    assert mismatch.value.code == "CONFIRMATION_MISMATCH"


def test_verified_publish_completes_and_records_result(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(task["task_id"], account="alpha", confirmed=True)

    completed = service.complete_publish(
        running["task_id"],
        {
            "verified": True,
            "status": "success",
            "evidence": "platform_response",
            "note_id": "note-1",
        },
    )

    assert completed["state"] == "SUCCESS"
    event = service.store.list("events")[0]
    assert event["task_id"] == task["task_id"]
    assert event["result"]["note_id"] == "note-1"


def test_unverified_publish_becomes_result_unknown(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(task["task_id"], account="alpha", confirmed=True)

    completed = service.complete_publish(
        running["task_id"],
        {"verified": False, "status": "result_unknown"},
    )

    assert completed["state"] == "RESULT_UNKNOWN"
    assert completed["error_code"] == "PUBLISH_RESULT_UNKNOWN"
    assert "不要直接重复发布" in completed["recommended_action"]


def test_publish_success_without_accepted_evidence_is_result_unknown(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(task["task_id"], account="alpha", confirmed=True)

    completed = service.complete_publish(
        running["task_id"],
        {"verified": True, "status": "success", "source": "unverified_caller"},
    )

    assert completed["state"] == "RESULT_UNKNOWN"
    assert completed["error_code"] == "PUBLISH_RESULT_UNKNOWN"


def test_saving_draft_cancels_publish_without_claiming_success(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.resume_preparation(task["task_id"], account="alpha")

    completed = service.complete_saved_draft(running["task_id"])

    assert completed["state"] == "CANCELLED"
    assert "草稿箱" in completed["result_summary"]
    assert service.store.list("events")[0]["result"]["saved_as_draft"] is True


def test_account_has_only_one_open_publish_workflow(tmp_path) -> None:
    service = _service(tmp_path)
    _waiting_task(service)

    with pytest.raises(ServiceError) as exc_info:
        service.begin(
            account="alpha",
            capability="fill-publish-video",
            request_summary="另一个发布任务",
            preview={**_preview(), "kind": "video"},
        )

    assert exc_info.value.code == "PUBLISH_WORKFLOW_ACTIVE"
