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


def register_commands(subparsers: Any) -> None:
    snapshot = subparsers.add_parser("browser-snapshot", help="截图并交给 Agent 图片工具读取")
    snapshot.add_argument("--output-file", required=True)
    snapshot.add_argument("--tab-id", type=int)
    snapshot.set_defaults(func=cmd_snapshot, allow_during_switch=True)
    action = subparsers.add_parser("browser-action", help="依据截图执行视觉操作并返回操作后截图")
    action.add_argument("--snapshot-file", required=True)
    action.add_argument("--input-file", required=True)
    action.add_argument("--output-file", required=True)
    action.set_defaults(func=cmd_action)
