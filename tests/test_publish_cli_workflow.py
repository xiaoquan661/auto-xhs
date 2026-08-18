from __future__ import annotations

import argparse
import json

import pytest

from product_store import ProductStore
from scripts.application_service import ApplicationService
from scripts.cli import cmd_click_publish, cmd_fill_publish, cmd_publish
from service_errors import ServiceError


class Browser:
    def close(self) -> None:
        pass


def test_legacy_one_step_publish_is_disabled(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cmd_publish(argparse.Namespace())

    report = json.loads(capsys.readouterr().out)
    assert exc_info.value.code == 2
    assert report["error_code"] == "ONE_STEP_PUBLISH_DISABLED"


def test_web_service_cannot_create_publish_execution_task(tmp_path) -> None:
    service = ApplicationService(product_store=ProductStore(tmp_path / "product"))

    with pytest.raises(ServiceError) as exc_info:
        service.create_task(
            source="webui",
            account_slot="alpha",
            capability="fill-publish",
            request_summary="尝试从 WebUI 创建发布任务",
            parameters={},
        )

    assert exc_info.value.code == "AGENT_CLI_ONLY"


def test_fill_then_confirmed_click_persists_agent_task(
    tmp_path, monkeypatch, capsys
) -> None:
    product_root = tmp_path / "product"
    title_file = tmp_path / "title.txt"
    content_file = tmp_path / "content.txt"
    image_file = tmp_path / "image.jpg"
    title_file.write_text("测试标题", encoding="utf-8")
    content_file.write_text("测试正文", encoding="utf-8")
    image_file.write_bytes(b"image")
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product_root))
    monkeypatch.setattr("image_downloader.process_images", lambda _images: [str(image_file)])
    monkeypatch.setattr("scripts.cli._connect", lambda _args: (Browser(), object()))
    monkeypatch.setattr("xhs.publish.fill_publish_form", lambda _page, _content: None)

    fill_args = argparse.Namespace(
        account="alpha",
        title_file=str(title_file),
        content_file=str(content_file),
        images=[str(image_file)],
        tags=["测试"],
        schedule_at=None,
        original=False,
        visibility="公开可见",
    )
    with pytest.raises(SystemExit) as fill_exit:
        cmd_fill_publish(fill_args)
    fill_report = json.loads(capsys.readouterr().out)

    assert fill_exit.value.code == 0
    assert fill_report["task"]["source"] == "agent"
    assert fill_report["task"]["state"] == "WAITING_APPROVAL"
    assert fill_report["preview"]["title"] == "测试标题"

    monkeypatch.setattr("scripts.cli._connect_existing", lambda _args: (Browser(), object()))
    monkeypatch.setattr(
        "xhs.publish.click_publish_button",
        lambda _page, **_kwargs: {
            "verified": True,
            "status": "success",
            "evidence": "platform_response",
            "note_id": "note-1",
        },
    )
    click_args = argparse.Namespace(
        account="alpha",
        task_id=fill_report["task_id"],
        confirm=True,
    )
    with pytest.raises(SystemExit) as click_exit:
        cmd_click_publish(click_args)
    click_report = json.loads(capsys.readouterr().out)

    assert click_exit.value.code == 0
    assert click_report["task"]["state"] == "SUCCESS"
    stored = ProductStore(product_root).get("tasks", fill_report["task_id"])
    assert stored["state"] == "SUCCESS"
    assert ProductStore(product_root).list("events")[0]["result"]["note_id"] == "note-1"
