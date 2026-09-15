from __future__ import annotations

import argparse
import json

import pytest
from product_store import ProductStore
from service_errors import ServiceError

from scripts.application_service import ApplicationService
from scripts.cli import (
    cmd_click_publish,
    cmd_fill_publish_video,
    cmd_long_article,
    cmd_next_step,
    cmd_publish,
    cmd_select_template,
)


class Browser:
    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def publish_calls(monkeypatch):
    calls = []

    def submit(page, **kwargs):
        calls.append(kwargs)
        return {
            "verified": True,
            "status": "success",
            "evidence": "platform_response",
            "note_id": "note-1",
        }

    monkeypatch.setattr("xhs.publish.click_publish_button", submit)
    return calls


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


def test_publish_fills_and_submits_once_with_platform_receipt(
    tmp_path, monkeypatch, capsys, publish_calls
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
    monkeypatch.setattr("scripts.cli._account_platform", lambda _account: "rednote")
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
        cmd_publish(fill_args)
    fill_report = json.loads(capsys.readouterr().out)

    assert fill_exit.value.code == 0
    assert fill_report["task"]["source"] == "agent"
    assert fill_report["task"]["platform"] == "rednote"
    assert fill_report["task"]["state"] == "SUCCESS"
    assert fill_report["task"]["parameters"]["preview"]["title"] == "测试标题"
    assert publish_calls == [{"expected_title": "测试标题"}]
    stored = ProductStore(product_root).get("tasks", fill_report["task_id"])
    assert stored["state"] == "SUCCESS"
    assert ProductStore(product_root).list("events")[0]["result"]["note_id"] == "note-1"
    with pytest.raises(ServiceError) as duplicate:
        cmd_click_publish(
            argparse.Namespace(
                account="alpha", task_id=fill_report["task_id"], confirm=False, preview_revision=""
            )
        )
    assert duplicate.value.code == "INVALID_TASK_STATE"
    assert len(publish_calls) == 1


def test_video_fill_publishes_with_platform_receipt(tmp_path, monkeypatch, capsys) -> None:
    product_root = tmp_path / "product"
    title_file = tmp_path / "title.txt"
    content_file = tmp_path / "content.txt"
    video_file = tmp_path / "video.mp4"
    title_file.write_text("视频标题", encoding="utf-8")
    content_file.write_text("视频正文", encoding="utf-8")
    video_file.write_bytes(b"video")
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product_root))
    monkeypatch.setattr("scripts.cli._account_platform", lambda _account: "xiaohongshu")
    monkeypatch.setattr("scripts.cli._connect", lambda _args: (Browser(), object()))
    monkeypatch.setattr("xhs.publish_video.fill_publish_video_form", lambda *_args: None)

    with pytest.raises(SystemExit) as exit_info:
        cmd_fill_publish_video(
            argparse.Namespace(
                account="alpha",
                title_file=str(title_file),
                content_file=str(content_file),
                video=str(video_file),
                tags=["测试"],
                schedule_at=None,
                visibility="公开可见",
            )
        )
    report = json.loads(capsys.readouterr().out)

    assert exit_info.value.code == 0
    assert report["task"]["state"] == "SUCCESS"
    assert report["task"]["parameters"]["stage"] == "publishing"
    assert report["task"]["parameters"]["preview"]["kind"] == "video"


def test_long_article_publishes_after_template_and_description(
    tmp_path, monkeypatch, capsys
) -> None:
    product_root = tmp_path / "product"
    title_file = tmp_path / "title.txt"
    content_file = tmp_path / "content.txt"
    description_file = tmp_path / "description.txt"
    title_file.write_text("长文标题", encoding="utf-8")
    content_file.write_text("长文正文", encoding="utf-8")
    description_file.write_text("长文摘要", encoding="utf-8")
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product_root))
    monkeypatch.setattr("scripts.cli._account_platform", lambda _account: "xiaohongshu")
    monkeypatch.setattr("scripts.cli._connect", lambda _args: (Browser(), object()))
    monkeypatch.setattr("scripts.cli._connect_existing", lambda _args: (Browser(), object()))
    monkeypatch.setattr(
        "xhs.publish_long_article.publish_long_article",
        lambda *_args, **_kwargs: ["简约"],
    )
    monkeypatch.setattr("xhs.publish_long_article.select_template", lambda *_args: True)
    monkeypatch.setattr(
        "xhs.publish_long_article.click_next_and_fill_description",
        lambda *_args: None,
    )

    with pytest.raises(SystemExit):
        cmd_long_article(
            argparse.Namespace(
                account="alpha",
                title_file=str(title_file),
                content_file=str(content_file),
                images=[],
            )
        )
    created = json.loads(capsys.readouterr().out)
    assert created["task"]["parameters"]["stage"] == "template_selection"

    with pytest.raises(SystemExit):
        cmd_select_template(
            argparse.Namespace(account="alpha", task_id=created["task_id"], name="简约")
        )
    selected = json.loads(capsys.readouterr().out)
    assert selected["task"]["parameters"]["stage"] == "next_step"

    with pytest.raises(SystemExit):
        cmd_next_step(
            argparse.Namespace(
                account="alpha",
                task_id=created["task_id"],
                content_file=str(description_file),
            )
        )
    preview = json.loads(capsys.readouterr().out)

    assert preview["task"]["state"] == "SUCCESS"
    assert preview["task"]["parameters"]["preview"]["kind"] == "long_article"
    assert preview["task"]["parameters"]["preview"]["description"] == "长文摘要"
