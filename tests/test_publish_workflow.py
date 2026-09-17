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


def _revision(task: dict) -> str:
    return task["parameters"]["preview_revision"]


def test_publish_workflow_persists_preview_for_webui_monitoring(tmp_path) -> None:
    service = _service(tmp_path)

    task = _waiting_task(service)
    persisted = service.tasks.get(task["task_id"])

    assert persisted["source"] == "agent"
    assert persisted["state"] == "RUNNING"
    assert persisted["parameters"]["stage"] == "preview_ready"
    assert persisted["parameters"]["preview"]["title"] == "测试标题"


def test_publish_workflow_persists_explicit_platform(tmp_path) -> None:
    service = _service(tmp_path)

    task = service.begin(
        account="rednote-a",
        platform="rednote",
        capability="fill-publish",
        request_summary="RedNote 图文发布预览",
        preview=_preview(),
    )

    assert task["platform"] == "rednote"


def test_publish_proceeds_with_bound_account_without_preview_approval(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(task["task_id"], account="alpha")
    assert running["state"] == "RUNNING"
    with pytest.raises(ServiceError) as mismatch:
        service.prepare_publish(task["task_id"], account="beta")
    assert mismatch.value.code == "CONFIRMATION_MISMATCH"


def test_verified_publish_completes_and_records_result(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=_revision(task),
    )

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
    published = service.published_posts.list()
    assert len(published) == 1
    assert published[0]["platform_post_id"] == "note-1"
    assert published[0]["result_state"] == "SUCCESS"


def test_unverified_publish_becomes_result_unknown(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=_revision(task),
    )

    completed = service.complete_publish(
        running["task_id"],
        {"verified": False, "status": "result_unknown"},
    )

    assert completed["state"] == "RESULT_UNKNOWN"
    assert completed["error_code"] == "PUBLISH_RESULT_UNKNOWN"
    assert "不要直接重复发布" in completed["recommended_action"]
    assert service.published_posts.list()[0]["result_state"] == "RESULT_UNKNOWN"


def test_publish_success_without_accepted_evidence_is_result_unknown(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=_revision(task),
    )

    completed = service.complete_publish(
        running["task_id"],
        {"verified": True, "status": "success", "source": "unverified_caller"},
    )

    assert completed["state"] == "RESULT_UNKNOWN"
    assert completed["error_code"] == "PUBLISH_RESULT_UNKNOWN"


def test_saving_draft_cancels_publish_without_claiming_success(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=_revision(task),
    )

    completed = service.complete_saved_draft(
        running["task_id"],
        {
            "verified": True,
            "status": "saved_draft",
            "evidence": "draft_success_toast",
            "saved_as_draft": True,
        },
    )

    assert completed["state"] == "CANCELLED"
    assert "草稿箱" in completed["result_summary"]
    assert service.store.list("events")[0]["result"]["saved_as_draft"] is True
    assert service.published_posts.list() == []


def test_changed_preview_revision_is_rejected(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)

    with pytest.raises(ServiceError) as exc_info:
        service.prepare_publish(
            task["task_id"],
            account="alpha",
            confirmed=True,
            preview_revision="older-preview",
        )

    assert exc_info.value.code == "PUBLISH_PREVIEW_CHANGED"
    assert service.get(task["task_id"], account="alpha")["state"] == "RUNNING"


def test_submitted_task_cannot_publish_twice(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    service.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=_revision(task),
    )

    with pytest.raises(ServiceError) as exc_info:
        service.prepare_publish(
            task["task_id"],
            account="alpha",
            confirmed=True,
            preview_revision=_revision(task),
        )

    assert exc_info.value.code == "INVALID_TASK_STATE"


def test_image_publish_requires_material(tmp_path) -> None:
    service = _service(tmp_path)

    with pytest.raises(ServiceError) as exc_info:
        service.begin(
            account="alpha",
            capability="fill-publish",
            request_summary="缺少图片",
            preview={**_preview(), "assets": [], "asset_count": 0},
        )

    assert exc_info.value.code == "PUBLISH_ASSET_REQUIRED"


def test_unverified_draft_save_becomes_result_unknown(tmp_path) -> None:
    service = _service(tmp_path)
    task = _waiting_task(service)
    running = service.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=_revision(task),
    )

    completed = service.complete_saved_draft(
        running["task_id"],
        {"verified": False, "status": "result_unknown", "evidence": "none"},
    )

    assert completed["state"] == "RESULT_UNKNOWN"
    assert completed["error_code"] == "PUBLISH_DRAFT_RESULT_UNKNOWN"
    assert service.published_posts.list() == []


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
