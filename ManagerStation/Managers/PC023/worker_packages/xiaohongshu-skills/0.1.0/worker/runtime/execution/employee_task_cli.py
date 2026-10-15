"""Stable file-based CLI adapter for an employee task."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import argparse
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path


def read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("输入文件必须是 JSON 对象")
    return value


def write_object(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def invoke(root: Path, request_file: Path, cli_main: Callable[[], None] | None = None) -> int:
    """Read native argv JSON, bind the task account, and retain stdout/stderr."""
    task = read_object(root / "task.json")
    request = read_object(request_file)
    args = request.get("args")
    from employee_recovery import READ_COMMANDS, start_step

    progress_path = root / "step-progress.json"
    progress = read_object(progress_path) if progress_path.exists() else {}
    previous = progress.get(request_file.name) or {}
    if (isinstance(args, list) and args and args[0] not in READ_COMMANDS
            and previous.get("args") == args):
        if previous.get("state") == "COMPLETED":
            return int(previous.get("exit_code") or 0)
        if previous.get("state") in {"RUNNING", "NEEDS_RECOVERY"}:
            # Retain the original submission evidence rather than resending it.
            return 2
    output = request_file.with_suffix(".output.json")
    error = request_file.with_suffix(".stderr.txt")
    old_argv = sys.argv
    code = 0
    started = datetime.now(UTC).isoformat()
    clock = time.monotonic()
    trace_path = request_file.with_suffix(".bridge.jsonl")
    old_trace = os.environ.get("XHS_EXECUTION_TRACE_FILE")
    os.environ["XHS_EXECUTION_TRACE_FILE"] = str(trace_path)
    try:
        with (
            output.open("w", encoding="utf-8") as out,
            error.open("w", encoding="utf-8") as err,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                if (not isinstance(args, list) or not args
                        or not all(isinstance(x, str) for x in args)):
                    raise ValueError("args 必须是以业务子命令开头的字符串数组")
                if ((args[0].startswith("-") and args != ["--help"])
                        or any(x == "--account" or x.startswith("--account=") for x in args)):
                    raise ValueError("账号由任务槽位指定；查询帮助请用 task-entry.cmd help")
                if cli_main is None:
                    from cli import main as cli_main
                if (
                    args[0] in {"check-login", "account-identity"}
                    and "--help" not in args
                    and task.get("capability") != args[0]
                ):
                    raise ValueError("普通任务复用绑定身份；身份检查请由用户单独发起")
                effective_args = list(args)
                if args[0] == "home-engagement" and "--help" not in args:
                    for feed_id in (task.get("parameters") or {}).get("excluded_feed_ids", []):
                        effective_args.extend(["--exclude-feed-id", str(feed_id)])
                sys.argv = ["cli", "--account", task["account_slot"], *effective_args]
                start_step(root, request_file, args)
                try:
                    cli_main()
                except SystemExit as exc:
                    code = int(exc.code or 0)
                out.flush()
                first = output.read_text(encoding="utf-8")
                try:
                    error_text = str(json.loads(first).get("error") or "")
                except ValueError:
                    error_text = first
                if (code and args[0] in {"browser-snapshot", "list-feeds", "get-feed-detail"}
                        and any(t in error_text for t in (
                            "页面已变化", "截图步骤超时", "导航超时"))):
                    request_file.with_suffix(".attempt1.output.json").write_text(
                        first, encoding="utf-8"
                    )
                    out.seek(0)
                    out.truncate()
                    code = 0
                    cli_main()
            except SystemExit as exc:
                code = int(exc.code or 0)
            except Exception as exc:
                code = 2
                print(json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False))
    finally:
        sys.argv = old_argv
        if old_trace is None:
            os.environ.pop("XHS_EXECUTION_TRACE_FILE", None)
        else:
            os.environ["XHS_EXECUTION_TRACE_FILE"] = old_trace
    from employee_recovery import record_progress

    recovery = record_progress(
        root, request_file, args if isinstance(args, list) else [], output, code
    )
    write_object(
        request_file.with_suffix(".execution.json"),
        {
            "task_id": task["task_id"],
            "command": args[0] if isinstance(args, list) and args else "",
            "exit_code": code,
            "started_at": started,
            "finished_at": datetime.now(UTC).isoformat(),
            "output_file": str(output),
            "stderr_file": str(error),
            "elapsed_seconds": round(time.monotonic() - clock, 3),
            "bridge_trace_file": str(trace_path),
            "recovery": recovery,
        },
    )
    return code


def stage_bug(root: Path, request_file: Path) -> dict:
    """Leave publication to the runner that owns the configured outbox."""
    task = read_object(root / "task.json")
    payload = read_object(request_file)
    payload.update(
        account_slot=task["account_slot"], task_id=task["task_id"],
        remote_execution_id=task["remote_execution_id"],
        evidence_index={"agent_run": str(root), "report_file": str(request_file)},
    )
    pending = root / "pending-bugs"
    pending.mkdir(exist_ok=True)
    destination = pending / request_file.name
    temporary = destination.with_suffix(".tmp")
    write_object(temporary, payload)
    temporary.replace(destination)
    return {"success": True, "state": "QUEUED", "report_file": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description="员工任务固定文件执行入口")
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("action", choices=["claim", "help", "invoke", "finish", "report-bug"])
    parser.add_argument("--command", nargs="*", default=[])
    parser.add_argument("--input-file", type=Path)
    args = parser.parse_args()
    root = args.run_root.resolve()
    if args.input_file is not None:
        args.input_file = (root / args.input_file).resolve()
    task = read_object(root / "task.json")
    if args.action == "claim":
        write_object(
            root / "claimed.json",
            {
                "task_id": task["task_id"],
                "account_slot": task["account_slot"],
                "claimed_at": datetime.now(UTC).isoformat(),
                "summary": task.get("request_summary", ""),
            },
        )
        brief = {**task, "parameters": dict(task.get("parameters") or {})}
        memory = brief["parameters"].pop("memory_context", None)
        if memory is not None:
            write_object(root / "memory-context.json", memory)
            brief["memory_context_file"] = str(root / "memory-context.json")
        print(json.dumps(brief, ensure_ascii=False))
    elif args.action == "help":
        request = root / "command-help.json"
        write_object(request, {"args": [*args.command, "--help"]})
        code = invoke(root, request)
        print(request.with_suffix(".output.json").read_text(encoding="utf-8"))
        raise SystemExit(code)
    elif args.action == "report-bug":
        if args.input_file is None or not args.input_file.is_absolute():
            parser.error("report-bug 需要 --input-file 绝对路径")
        print(json.dumps(stage_bug(root, args.input_file), ensure_ascii=False))
    elif args.action == "invoke":
        if args.input_file is None or not args.input_file.is_absolute():
            parser.error("invoke 需要 --input-file 绝对路径")
        code = invoke(root, args.input_file)
        output = args.input_file.with_suffix(".output.json")
        content = output.read_text(encoding="utf-8")
        recovery_path = args.input_file.with_suffix(".recovery.json")
        if recovery_path.exists():
            print(json.dumps({"recovery": read_object(recovery_path)}, ensure_ascii=False))
        print(content if len(content) <= 12000 else json.dumps(
            {"output_file": str(output), "exit_code": code}, ensure_ascii=False
        ))
        raise SystemExit(code)
    else:
        result = read_object(root / "execution-result.json")
        if not isinstance(result.get("business_result"), dict) or not isinstance(
            result.get("item_results"), list
        ):
            parser.error("回执需要 business_result 对象和 item_results 数组")
        print(json.dumps({"result_file": "execution-result.json"}))


if __name__ == "__main__":
    main()
