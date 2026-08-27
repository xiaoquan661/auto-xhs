from __future__ import annotations

from scripts.operations_db import OperationsDatabase
from scripts.published_post_service import PublishedPostService


def test_publish_result_creates_one_queryable_post_record(tmp_path) -> None:
    service = PublishedPostService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )
    task = {
        "task_id": "publish-task-1",
        "account_slot": "alpha",
        "platform": "xiaohongshu",
        "capability": "fill-publish",
        "state": "SUCCESS",
        "finished_at": "2026-08-26T10:00:00+00:00",
        "result_summary": "发布成功，平台响应已核验",
        "error_code": "",
        "parameters": {
            "verified_uid": "uid-alpha",
            "preview": {
                "kind": "image",
                "title": "测试标题",
                "draft_id": "draft-1",
                "draft_revision": "revision-2",
                "source_content_id": "source-3",
            },
        },
    }

    first = service.record(
        task,
        result={
            "verified": True,
            "note_id": "note-1",
            "url": "https://www.xiaohongshu.com/explore/note-1",
            "readback": {"published": True},
        },
        operation_event_id="operation-1",
    )
    duplicate = service.record(
        task,
        result={"verified": True, "note_id": "note-1"},
        operation_event_id="operation-1",
    )

    assert first["published_post_id"] == duplicate["published_post_id"]
    assert first["platform_post_id"] == "note-1"
    assert first["verified_uid"] == "uid-alpha"
    assert first["title"] == "测试标题"
    assert first["readback"]["published"] is True
    assert len(service.list(account_slot="alpha")) == 1


def test_unknown_publish_attempt_is_kept_without_claiming_a_post_id(tmp_path) -> None:
    service = PublishedPostService(
        OperationsDatabase(path=tmp_path / "operations.db")
    )
    record = service.record(
        {
            "task_id": "publish-task-2",
            "account_slot": "alpha",
            "capability": "fill-publish",
            "state": "RESULT_UNKNOWN",
            "finished_at": "2026-08-26T10:00:00+00:00",
            "error_code": "PUBLISH_RESULT_UNKNOWN",
            "result_summary": "发布结果需要核验",
            "parameters": {"preview": {"title": "待核验标题"}},
        },
        result={"verified": False},
    )

    assert record["result_state"] == "RESULT_UNKNOWN"
    assert record["platform_post_id"] == ""
    assert record["published_at"] is None
