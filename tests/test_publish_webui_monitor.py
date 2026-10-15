from __future__ import annotations

import pytest
from business_runner import BusinessRunner
from product_store import ProductStore
from project_paths import webui_asset_path
from publish_workflow import PublishWorkflowService
from service_errors import ServiceError

from scripts.application_service import ApplicationService


def _waiting_publish(store: ProductStore) -> dict:
    workflow = PublishWorkflowService(store)
    task = workflow.begin(
        account="alpha",
        capability="fill-publish",
        request_summary="图文发布预览：测试标题",
        preview={
            "kind": "image",
            "title": "测试标题",
            "content": "测试正文",
            "assets": [r"C:\Media\one.jpg"],
            "asset_count": 1,
            "tags": ["测试"],
            "visibility": "公开可见",
            "schedule_at": None,
        },
    )
    return workflow.wait_for_confirmation(
        task["task_id"],
        summary="图文已填写到浏览器，等待用户确认真实预览",
    )


def _ready_status(uid: str = "owner-1") -> dict:
    return {
        "ready": True,
        "status": "READY",
        "identity": {
            "user_id": uid,
            "live_user_id": uid,
            "matches_record": True,
        },
    }


def test_webui_can_resume_existing_prepared_publish_task() -> None:
    script = (webui_asset_path("manager-console.js")).read_text(encoding="utf-8")
    page = (webui_asset_path("index.html")).read_text(encoding="utf-8")

    assert '"fill-publish"' in script
    assert "function pendingPublishTasks()" in script
    assert "function openPublishDialog(taskId)" in script
    assert 'task.parameters?.stage === "preview_ready"' in script
    assert "`${api}/tasks/${task.task_id}/${action}`" in script
    assert 'id="publish-dialog-title">继续正式发布' in page
    assert "创建发布任务" not in page
    assert "preview_revision: task.parameters?.preview_revision" in script
    assert 'id="publish-confirm-dialog"' in page
    assert 'id="publish-preview-confirmed"' not in page
    assert 'id="publish-save-draft"' in page
    assert 'id="publish-confirm-submit"' in page


@pytest.mark.parametrize("identity_source", ["live_user_id", "execution_user_id"])
def test_webui_confirmation_reuses_publish_workflow(tmp_path, identity_source) -> None:
    store = ProductStore(tmp_path / "product")
    calls = []
    runner = BusinessRunner(
        executor=lambda account, capability, parameters: (
            calls.append((account, capability, parameters))
            or {
                "verified": True,
                "status": "success",
                "evidence": "platform_response",
                "note_id": "note-1",
            }
        )
    )
    service = ApplicationService(product_store=store, business_runner=runner)
    status = _ready_status()
    status["identity"].pop("live_user_id")
    status["identity"][identity_source] = "owner-1"
    service.get_account_status = lambda account: status
    task = _waiting_publish(store)

    result = service.confirm_publish_task(
        task["task_id"],
        account_slot="alpha",
        verified_uid="owner-1",
        confirmed=True,
        preview_confirmed=True,
        preview_revision=task["parameters"]["preview_revision"],
    )

    assert result["task"]["state"] == "SUCCESS"
    assert calls == [("alpha", "click-publish", {"expected_title": "测试标题"})]
    assert store.list("events")[0]["result"]["note_id"] == "note-1"


def test_webui_publish_confirmation_rejects_identity_mismatch(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    service = ApplicationService(
        product_store=store,
        business_runner=BusinessRunner(executor=lambda *_args: {}),
    )
    service.get_account_status = lambda account: _ready_status("owner-2")
    task = _waiting_publish(store)

    with pytest.raises(ServiceError) as exc_info:
        service.confirm_publish_task(
            task["task_id"],
            account_slot="alpha",
            verified_uid="owner-1",
            confirmed=True,
            preview_confirmed=True,
            preview_revision=task["parameters"]["preview_revision"],
        )

    assert exc_info.value.code == "CONFIRMATION_MISMATCH"
    assert store.get("tasks", task["task_id"])["state"] == "RUNNING"


def test_webui_can_save_waiting_publish_as_draft(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    calls = []
    service = ApplicationService(
        product_store=store,
        business_runner=BusinessRunner(
            executor=lambda account, capability, parameters: (
                calls.append((account, capability, parameters))
                or {
                    "verified": True,
                    "status": "saved_draft",
                    "evidence": "draft_manager_readback",
                    "saved_as_draft": True,
                }
            )
        ),
    )
    service.get_account_status = lambda account: _ready_status()
    task = _waiting_publish(store)

    result = service.save_publish_task_as_draft(
        task["task_id"],
        account_slot="alpha",
        verified_uid="owner-1",
        confirmed=True,
        preview_revision=task["parameters"]["preview_revision"],
    )

    assert result["task"]["state"] == "CANCELLED"
    assert calls == [("alpha", "save-draft", {"expected_title": "测试标题"})]
    assert store.list("events")[0]["result"]["saved_as_draft"] is True


@pytest.mark.parametrize("verified", [True, False])
def test_manager_task_fills_publishes_and_records_final_result(tmp_path, verified):
    store = ProductStore(tmp_path / "product")
    calls = []

    def execute(account, capability, parameters):
        calls.append(capability)
        if capability == "fill-publish":
            return {"filled": True, "published": False}
        return {
            "verified": verified,
            "evidence": "platform_response" if verified else "",
            "note_id": "note-1" if verified else "",
        }

    service = ApplicationService(
        product_store=store, business_runner=BusinessRunner(executor=execute)
    )
    service.get_account_status = lambda _: _ready_status()
    service._resolve_account_platform = lambda _: "xiaohongshu"
    created = service.create_task(
        source="agent",
        source_type="manager-agent",
        account_slot="alpha",
        platform="xiaohongshu",
        capability="fill-publish",
        request_summary="发布测试内容",
        parameters={
            "preview": {
                "kind": "image",
                "title": "测试标题",
                "content": "测试正文",
                "assets": ["C:/Media/test.jpg"],
                "asset_count": 1,
            }
        },
    )
    result = service.execute_task(created["task"]["task_id"])
    assert calls == ["fill-publish", "click-publish"]
    assert result["task"]["state"] == ("SUCCESS" if verified else "RESULT_UNKNOWN")
    assert store.list("events")[0]["result"]["published"] is (True if verified else None)
    assert len(service.publish_workflows.published_posts.list()) == 1
