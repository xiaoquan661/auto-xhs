"""File-based browser images and actions for the employee Agent."""

from __future__ import annotations

import base64
import json
import struct
from pathlib import Path
from typing import Any

from xhs.errors import XHSError


def save_snapshot(page: Any, destination: Path, account: str, tab_id: int | None = None) -> dict:
    if not destination.is_absolute() or destination.suffix.lower() != ".png":
        raise XHSError("截图输出需要 PNG 绝对路径")
    snapshot = page.visual_snapshot(tab_id)
    png = base64.b64decode(snapshot.pop("data"))
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        raise XHSError("扩展未返回 PNG 图片")
    width, height = struct.unpack(">II", png[16:24])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(png)
    metadata = destination.with_suffix(".snapshot.json")
    result = {
        **snapshot,
        "account": account,
        "image_path": str(destination),
        "snapshot_file": str(metadata),
        "image_width": width,
        "image_height": height,
        "success": True,
        "next_action": "使用 view_image 工具打开 image_path 识别图片",
    }
    metadata.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def act_on_snapshot(
    page: Any, snapshot_file: Path, action_file: Path, destination: Path, account: str
) -> dict:
    if not all(path.is_absolute() for path in (snapshot_file, action_file, destination)):
        raise XHSError("视觉操作的文件路径必须是绝对路径")
    if destination.suffix.lower() != ".png":
        raise XHSError("操作后截图需要 PNG 输出路径")
    snapshot = json.loads(snapshot_file.read_text(encoding="utf-8"))
    action = json.loads(action_file.read_text(encoding="utf-8"))
    if snapshot["account"] != account:
        raise XHSError("截图所属账号与当前槽位不一致")
    if not action.get("intent"):
        raise XHSError("视觉操作需要填写 intent，说明本次操作目的")
    if action.get("action") not in {"click", "scroll", "type", "key"}:
        raise XHSError("视觉操作支持 click、scroll、type、key")
    # Agent coordinates refer to the original PNG pixels; CDP uses CSS viewport pixels.
    if action["action"] in {"click", "scroll"}:
        action = {
            **action,
            "x": float(action["x"]) * snapshot["frame"]["width"] / snapshot["image_width"],
            "y": float(action["y"]) * snapshot["frame"]["height"] / snapshot["image_height"],
        }
    if action["action"] == "type":
        action["text"] = Path(action["text_file"]).read_text(encoding="utf-8")
    result = page.visual_action(snapshot, action)
    try:
        after = save_snapshot(page, destination, account, snapshot["tab_id"])
    except Exception as exc:
        return {
            "success": False,
            "status": "RESULT_UNKNOWN",
            "dispatched": True,
            "error": str(exc),
            "next_action": "操作已发出，请重新截图核对，保持当前步骤",
        }
    return {
        **after,
        "action_result": result,
        "status": "OBSERVATION_REQUIRED",
        "next_action": "使用 view_image 查看操作后截图，确认页面结果后继续",
    }


def cmd_snapshot(args: Any) -> None:
    from cli import _connect_runtime, _output

    account, page = _connect_runtime(args)
    _output(save_snapshot(page, Path(args.output_file), account, args.tab_id))


def cmd_action(args: Any) -> None:
    from application_service import ApplicationService
    from cli import _connect_runtime, _output

    ApplicationService().require_account_identity(args.account)
    account, page = _connect_runtime(args)
    _output(
        act_on_snapshot(
            page, Path(args.snapshot_file), Path(args.input_file), Path(args.output_file), account
        )
    )


def cmd_publish_page_status(args: Any) -> None:
    from cli import _connect_runtime, _output
    from xhs.publish import _select_publish_preview

    _, page = _connect_runtime(args)
    title = Path(args.title_file).read_text(encoding="utf-8").strip()
    _select_publish_preview(page, title)
    _output(page.evaluate("""(() => {
        const host = document.querySelector('xhs-publish-btn[is-publish="true"]');
        const rect = host?.getBoundingClientRect();
        return {url: location.href, width: innerWidth, height: innerHeight,
            dpr: devicePixelRatio, scrollX, scrollY,
            viewport: visualViewport ? {scale: visualViewport.scale,
                offsetLeft: visualViewport.offsetLeft, offsetTop: visualViewport.offsetTop} : null,
            host: rect ? {x: rect.x, y: rect.y, width: rect.width, height: rect.height,
                attributes: Array.from(host.attributes).map(a => [a.name,a.value])} : null,
            body: document.body.innerText.slice(-1800)};
    })()"""))


def cmd_publish_prepared_retry(args: Any) -> None:
    from cli import _connect_existing, _publish_prepared_form
    from publish_workflow import PublishWorkflowService
    from xhs.publish import _select_publish_preview

    if not args.confirm:
        raise XHSError("再次提交已准备表单需要用户明确授权")
    workflow = PublishWorkflowService()
    original = workflow.get(args.task_id, account=args.account)
    if original["state"] != "RESULT_UNKNOWN":
        raise XHSError("原任务不是待核对的发布任务")
    browser, page = _connect_existing(args)
    try:
        preview = original["parameters"]["preview"]
        _select_publish_preview(page, preview["title"])
        task = workflow.begin(account=args.account, platform=original.get("platform"),
                              capability=original["capability"],
                              request_summary="继续发布：" + preview["title"], preview=preview)
        workflow.tasks.update_parameters(task["task_id"], retry_of=args.task_id,
                                         user_confirmed_retry=True)
        task = workflow.wait_for_confirmation(task["task_id"], summary="原表单已核对，用户授权再次提交")
        _publish_prepared_form(workflow, task, page, args.account)
    finally:
        browser.close()


def register_commands(subparsers: Any) -> None:
    retry = subparsers.add_parser("publish-prepared-retry", help="经用户授权再次提交匹配的原发布表单")
    retry.add_argument("--task-id", required=True)
    retry.add_argument("--confirm", action="store_true")
    retry.set_defaults(func=cmd_publish_prepared_retry)
    status = subparsers.add_parser("publish-page-status", help="只读查看匹配发布页的布局与提示")
    status.add_argument("--title-file", required=True)
    status.set_defaults(func=cmd_publish_page_status)
    snapshot = subparsers.add_parser("browser-snapshot", help="截图并交给 Agent 图片工具读取")
    snapshot.add_argument("--output-file", required=True)
    snapshot.add_argument("--tab-id", type=int)
    snapshot.set_defaults(func=cmd_snapshot, allow_during_switch=True)
    action = subparsers.add_parser("browser-action", help="依据截图执行视觉操作并返回操作后截图")
    action.add_argument("--snapshot-file", required=True)
    action.add_argument("--input-file", required=True)
    action.add_argument("--output-file", required=True)
    action.set_defaults(func=cmd_action)
