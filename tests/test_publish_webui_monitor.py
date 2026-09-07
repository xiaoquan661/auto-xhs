from __future__ import annotations

from pathlib import Path

import pytest
from business_runner import BusinessRunner
from product_store import ProductStore
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


def test_webui_only_exposes_existing_publish_preview_confirmation() -> None:
    root = Path(__file__).parents[1]
    script = (root / "webui" / "manager-console.js").read_text(encoding="utf-8")
    page = (root / "webui" / "index.html").read_text(encoding="utf-8")

    assert "preview_revision: task.parameters?.preview_revision" in script
