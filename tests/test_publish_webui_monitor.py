from __future__ import annotations

from pathlib import Path

import pytest

from business_runner import BusinessRunner
from product_store import ProductStore
from publish_workflow import PublishWorkflowService
from scripts.application_service import ApplicationService
from service_errors import ServiceError


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


def test_webui_offers_controlled_publish_confirmation() -> None:
    root = Path(__file__).parents[1]
    script = "\n".join(
        (root / "webui" / name).read_text(encoding="utf-8")
        for name in ("task-catalog.js", "app.js")
    )
    page = (root / "webui" / "index.html").read_text(encoding="utf-8")

    assert '"fill-publish": { label: "图文发布" }' in script
    assert "appendPublishTaskPreview(card, item)" in script
    assert 'agentMonitorCapabilities = new Set([...publishMonitorCapabilities, "send-private-messages"])' in script
    assert "publishTaskReadyForConfirmation(item)" in script
    assert "openPublishConfirmation(item)" in script
    assert "tasks/${task.task_id}/${action}" in script
    assert 'id="publish-confirm-dialog"' in page
    assert 'id="publish-preview-confirmed"' in page
    assert 'id="publish-save-draft"' in page
    assert 'id="publish-confirm-submit"' in page


def test_webui_confirmation_reuses_publish_workflow(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    calls = []
    runner = BusinessRunner(
        executor=lambda account, capability, parameters: calls.append(
            (account, capability, parameters)
        )
        or {
            "verified": True,
            "status": "success",
            "evidence": "platform_response",
            "note_id": "note-1",
        }
    )
    service = ApplicationService(product_store=store, business_runner=runner)
    service.get_account_status = lambda account: _ready_status()
    task = _waiting_publish(store)

    result = service.confirm_publish_task(
        task["task_id"],
        account_slot="alpha",
        verified_uid="owner-1",
        confirmed=True,
        preview_confirmed=True,
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
        )

    assert exc_info.value.code == "CONFIRMATION_MISMATCH"
    assert store.get("tasks", task["task_id"])["state"] == "WAITING_APPROVAL"


def test_webui_can_save_waiting_publish_as_draft(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    calls = []
    service = ApplicationService(
        product_store=store,
        business_runner=BusinessRunner(
            executor=lambda account, capability, parameters: calls.append(
                (account, capability, parameters)
            )
            or {"success": True}
        ),
    )
    service.get_account_status = lambda account: _ready_status()
    task = _waiting_publish(store)

    result = service.save_publish_task_as_draft(
        task["task_id"],
        account_slot="alpha",
        verified_uid="owner-1",
        confirmed=True,
    )

    assert result["task"]["state"] == "CANCELLED"
    assert calls == [("alpha", "save-draft", {})]
    assert store.list("events")[0]["result"]["saved_as_draft"] is True
