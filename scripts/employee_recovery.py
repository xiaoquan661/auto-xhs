"""Describe recovery at the current task step without restarting business work."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

READ_COMMANDS = {"browser-snapshot", "list-feeds", "get-feed-detail", "search-feeds",
                 "private-message-context", "collect-operations-metrics",
                 "collect-note-comments", "collect-private-messages"}


def start_step(root: Path, request: Path, args: list[str]) -> None:
    path = root / "step-progress.json"
    progress = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    progress[request.name] = {
        "command": args[0] if args else "", "args": args, "state": "RUNNING",
        "started_at": datetime.now(UTC).isoformat(),
    }
    path.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")


def can_resume(root: Path) -> bool:
    path = root / "step-progress.json"
    progress = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return all(
        row.get("command") in READ_COMMANDS
        or (row.get("state") == "COMPLETED" and not row.get("recovery"))
        for row in progress.values()
    )


def recovery_for(payload: dict, exit_code: int) -> dict:
    values = [payload, payload.get("task", {}), payload.get("result", {})]
    states = {
        str(row.get("state") or row.get("status") or "") for row in values if isinstance(row, dict)
    }
    text = json.dumps(
        [
            {key: row.get(key) for key in ("error", "error_code", "state", "status")}
            for row in values
            if isinstance(row, dict)
        ],
        ensure_ascii=False,
    )
    if "RESULT_UNKNOWN" in text or "RECONCILIATION_REQUIRED" in text:
        kind, action = "reconcile", "核对当前动作的原始结果和平台回读，确认前保持当前步骤"
    elif any(
        code in text
        for code in (
            "LOGIN_REQUIRED",
            "IDENTITY_MISMATCH",
            "IDENTITY_CHECK_REQUIRED",
            "SWITCH_PENDING",
            "未登录",
            "身份不一致",
            "换号",
        )
    ):
        kind, action = "identity", "暂停当前账号并报告登录或身份异常，等待用户处理后继续"
    elif (
        exit_code
        or states & {"FAILED", "BLOCKED", "PARTIAL_SUCCESS", "UNKNOWN"}
        or (payload.get("success") is False)
    ):
        kind, action = "observe", "检查本步骤输出，必要时截图识别当前页面，从失败点继续"
    else:
        return {}
    return {
        "kind": kind,
        "next_action": action,
        "identity_check_required": False,
        "user_action_required": kind == "identity",
        "refresh_inbox": False,
        "resume_scope": "current_step",
    }


def record_progress(
    root: Path, request_file: Path, args: list[str], output: Path, exit_code: int
) -> dict:
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        payload = {"success": False, "error": "业务输出需要检查原始文件"}
    recovery = recovery_for(payload, exit_code) if isinstance(payload, dict) else {}
    progress_file = root / "step-progress.json"
    progress = (
        json.loads(progress_file.read_text(encoding="utf-8")) if progress_file.exists() else {}
    )
    progress[request_file.name] = {
        "command": args[0] if args else "",
        "exit_code": exit_code,
        "output_file": str(output),
        "recovery": recovery,
        "args": args,
        "state": "COMPLETED" if not recovery else "NEEDS_RECOVERY",
        "finished_at": datetime.now(UTC).isoformat(),
    }
    progress_file.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8")
    if recovery:
        request_file.with_suffix(".recovery.json").write_text(
            json.dumps(recovery, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    else:
        request_file.with_suffix(".recovery.json").unlink(missing_ok=True)
    return recovery
