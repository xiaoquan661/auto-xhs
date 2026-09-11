"""Run an inbox task with the employee's installed Codex and persist its receipts."""

from __future__ import annotations

import getpass
import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from .capability_registry import RiskLevel, get_capability_policy
    from .default_persona import friendly_commenter
except ImportError:
    from capability_registry import RiskLevel, get_capability_policy
    from default_persona import friendly_commenter


PROBE_CAPABILITY = "employee-agent-probe"
RESULT_STATES = ["SUCCESS", "PARTIAL_SUCCESS", "FAILED", "BLOCKED", "RESULT_UNKNOWN"]
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "result_file": {"type": "string", "enum": ["execution-result.json"]},
    },
    "required": ["result_file"],
}


def find_codex() -> str:
    executable = shutil.which("codex.exe") or shutil.which("codex")
    if executable:
        return executable
    installations = list(
        (Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI" / "Codex" / "bin").glob("*/codex.exe")
    )
    if installations:
        return str(max(installations, key=lambda path: path.stat().st_mtime))
    raise FileNotFoundError("员工机未找到 Codex，请先安装并登录员工 Agent")


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class EmployeeCodexRunner:
    def __init__(
        self,
        state_root: str | Path,
        *,
        project_root: str | Path | None = None,
        on_claim: Callable[[dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        on_bug: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.root = Path(state_root).resolve()
        self.project = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.on_claim = on_claim
        self.should_stop = should_stop
        self.on_bug = on_bug

    def __call__(self, task: dict[str, Any]) -> dict[str, Any]:
        if not (task.get("persona_snapshot") or (task.get("parameters") or {}).get("persona")):
            task = {**task, "persona_snapshot": friendly_commenter(
                str(task.get("machine_id") or ""), str(task.get("account_slot") or "")
            )}
        capability = str(task.get("capability") or "")
        if capability != PROBE_CAPABILITY:
            try:
                policy = get_capability_policy(capability)
            except (KeyError, ValueError):
                return {"state": "BLOCKED", "message": "任务能力未登记"}
            if not policy.enabled_in_v1:
                return {"state": "BLOCKED", "message": "任务能力尚未开放"}
            if policy.requires_confirmation and not (task.get("authorization") or {}).get(
                "authorization_id"
            ):
                return {"state": "BLOCKED", "message": "任务缺少用户确认授权"}
            task = {
                **task,
                "write_intent": bool(task.get("write_intent"))
                or (policy.risk_level != RiskLevel.READ_ONLY),
            }
        try:
            executable = find_codex()
        except FileNotFoundError as exc:
            return {"state": "BLOCKED", "message": str(exc)}

        # The worker creates this execution identifier before starting the detached runner.
        run_root = self.root / "agent-runs" / str(task["remote_execution_id"])
        run_root.mkdir(parents=True, exist_ok=True)
        task_path = run_root / "task.json"
        result_path = run_root / "result.json"
        claim_path = run_root / "claimed.json"
        write_json(task_path, task)
        write_json(run_root / "result-schema.json", RESULT_SCHEMA)
        self._write_entry(run_root)
        prompt = self._prompt(task, run_root)
        (run_root / "instructions.md").write_text(prompt, encoding="utf-8")
        command = [
            executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            str(run_root),
            "--sandbox",
            "workspace-write",
            "--add-dir",
            str(self.root),
            "--add-dir",
            str(self.project),
            "--add-dir",
            str(Path.home() / ".xhs"),
            "-c",
            "sandbox_workspace_write.network_access=true",
            "-c",
            'approval_policy="never"',
            "-c",
            'windows.sandbox="unelevated"',
            "--output-schema",
            str(run_root / "result-schema.json"),
            "-o",
            str(result_path),
            "-",
        ]
        claimed = False
        process = None
        event_reader = None
        write_json(run_root / "runner-context.json", {
            "started_at": datetime.now(UTC).isoformat(), "pid": os.getpid(),
            "user": getpass.getuser(), "state_root": str(self.root),
            "run_root": str(run_root), "sandbox": "workspace-write",
            "bug_delivery": "runner",
        })
        try:
            with (run_root / "events.jsonl").open("w", encoding="utf-8") as events:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    cwd=run_root,
                    env={**os.environ, "PYTHONUTF8": "1"},
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                event_reader = threading.Thread(
                    target=self._capture_events, args=(process, events, run_root), daemon=True,
                )
                event_reader.start()
                write_json(
                    run_root / "process.json", {"pid": process.pid, "task_id": task["task_id"]}
                )
                assert process.stdin is not None
                process.stdin.write(prompt)
                process.stdin.close()
                deadline = time.monotonic() + float(task.get("timeout_seconds") or 1800)
                while True:
                    if not claimed and claim_path.exists():
                        try:
                            claim = json.loads(claim_path.read_text(encoding="utf-8-sig"))
                        except (OSError, ValueError):
                            claim = {}
                        if claim.get("task_id") == task["task_id"]:
                            claimed = True
                            if self.on_claim:
                                self.on_claim({**claim, "evidence_path": str(claim_path)})
                    if process.poll() is not None:
                        break
                    if self._poll_stop(run_root):
                        self._stop(process)
                        return self._incomplete(task, "员工 Agent 已停止，执行结果待核对", run_root)
                    if time.monotonic() >= deadline:
                        self._stop(process)
                        return self._incomplete(
                            task, "员工 Agent 执行超时，执行结果待核对", run_root
                        )
                    time.sleep(1)
                event_reader.join(timeout=5)
            if not result_path.exists() and not (run_root / "execution-result.json").exists():
                return self._incomplete(task, "员工 Agent 未返回完整的领取回执和结果", run_root)
            result = (json.loads(result_path.read_text(encoding="utf-8-sig"))
                      if result_path.exists() else {"result_file": "execution-result.json"})
            if "result_file" in result:
                if result["result_file"] != "execution-result.json":
                    raise ValueError("执行结果文件名不匹配")
                result = json.loads(
                    (run_root / "execution-result.json").read_text(encoding="utf-8-sig")
                )
                business = result["business_result"]
                items = result["item_results"]
            else:
                # Read receipts created by workers using the previous result schema.
                business = json.loads(result["business_result_json"])
                items = json.loads(result["item_results_json"])
            if result["state"] not in RESULT_STATES or not isinstance(business, dict):
                raise ValueError("结果状态或业务数据格式错误")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("逐项执行结果格式错误")
            receipt_status = {
                "claimed": claimed,
                "agent_exit_code": process.returncode,
                "result_received": True,
            }
            if process.returncode != 0 or not claimed:
                # A startup failure can still have a complete, useful failure receipt.
                not_executed = (
                    result["state"] in {"FAILED", "BLOCKED"}
                    and business.get("business_executed") is False
                    and not items
                    and not list(run_root.glob("*.execution.json"))
                )
                if not not_executed:
                    result["state"] = "RESULT_UNKNOWN" if task.get("write_intent") else "FAILED"
                    result["message"] = f"执行流程未正常结束，结果待核对：{result['message']}"
                    items = []
                    business = {"reported_result": business}
                business["receipt_status"] = receipt_status
            if (capability == PROBE_CAPABILITY and result["state"] == "SUCCESS"
                    and business.get("probe_token") != (
                task.get("parameters") or {}
            ).get("probe_token")):
                raise ValueError("连接验证回执不匹配")
            if capability == "post-comment" and result["state"] != "RESULT_UNKNOWN":
                requested = int((task.get("parameters") or {}).get("count") or 1)
                for item in items:
                    if item.get("status") == "SUCCESS" and not item.get("platform_readback"):
                        item.update(status="RESULT_UNKNOWN", success=False)
                    item["success"] = item.get("status") == "SUCCESS"
                succeeded = sum(item["success"] for item in items)
                business.update(requested=requested, succeeded=succeeded)
                if any(item.get("status") == "RESULT_UNKNOWN" for item in items) or (
                    result["state"] == "SUCCESS" and not items
                ):
                    result["state"] = "RESULT_UNKNOWN"
                elif succeeded >= requested:
                    result["state"] = "SUCCESS"
                elif succeeded:
                    result["state"] = "PARTIAL_SUCCESS"
                elif result["state"] == "SUCCESS":
                    result["state"] = "FAILED"
            return {
                "state": result["state"],
                "message": result["message"],
                "business_result": business,
                "item_results": items,
                "evidence_index": {"agent_run": str(run_root), "paths": result["evidence_paths"]},
                "raw_result": result,
            }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            if process is not None and process.poll() is None:
                self._stop(process)
            return self._incomplete(task, f"员工 Agent 执行或结果读取失败：{exc}", run_root)
        finally:
            if event_reader is not None:
                event_reader.join(timeout=5)
            self._publish_bugs(run_root)
            write_json(run_root / "runner-finished.json", {
                "finished_at": datetime.now(UTC).isoformat(),
            })

    def _poll_stop(self, run_root: Path) -> bool:
        """读取停止指令；状态存储临时不可读时保持业务执行和既有超时限制。"""
        if not self.should_stop:
            return False
        try:
            return self.should_stop()
        except (OSError, RuntimeError) as exc:
            if not isinstance(exc, OSError) and getattr(exc, "code", "") not in {
                "STATE_BUSY", "STATE_UNREADABLE",
            }:
                raise
            with (run_root / "state-check-errors.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "occurred_at": datetime.now(UTC).isoformat(),
                    "stage": "stop_check", "error_type": type(exc).__name__,
                    "message": str(exc), "action": "continue_until_next_check",
                }, ensure_ascii=False) + "\n")
            return False

    @staticmethod
    def _capture_events(process: subprocess.Popen[str], events: Any, root: Path) -> None:
        """Retain original events and timestamp receipt without storing command contents twice."""
        assert process.stdout is not None
        with (root / "event-timing.jsonl").open("w", encoding="utf-8") as timing:
            for line in process.stdout:
                events.write(line)
                events.flush()
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                item = event.get("item") or {}
                timing.write(json.dumps({
                    "received_at": datetime.now(UTC).isoformat(),
                    "event": event.get("type"), "item_id": item.get("id"),
                    "item_type": item.get("type"),
                }) + "\n")
                timing.flush()

    def _publish_bugs(self, root: Path) -> None:
        if self.on_bug is None:
            return
        for path in sorted((root / "pending-bugs").glob("*.json")):
            receipt = path.with_suffix(".receipt")
            if receipt.exists():
                continue
            try:
                result = self.on_bug(json.loads(path.read_text(encoding="utf-8-sig")))
                if not result.get("success"):
                    raise ValueError(str(result))
                write_json(receipt, result)
            except (OSError, ValueError, RuntimeError) as exc:
                write_json(path.with_suffix(".error"), {"error": str(exc)})

    @staticmethod
    def _stop(process: subprocess.Popen[str]) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            process.kill()
        process.wait(timeout=15)

    @staticmethod
    def _incomplete(task: dict[str, Any], message: str, root: Path) -> dict[str, Any]:
        return {
            "state": "RESULT_UNKNOWN" if task.get("write_intent") else "FAILED",
            "message": message,
            "evidence_index": {"agent_run": str(root)},
        }

    def _write_entry(self, root: Path) -> None:
        """Keep Windows quoting in a tested launcher owned by the runner."""
        (root / "task-entry.cmd").write_text(
            '@echo off\nchcp 65001 >nul\n'
            f'"{self.project / ".venv" / "Scripts" / "python.exe"}" '
            f'"{self.project / "scripts" / "employee_task_cli.py"}" '
            f'--run-root "{root}" %*\nexit /b %errorlevel%\n',
            encoding="utf-8",
        )

    def _prompt(self, task: dict[str, Any], root: Path) -> str:
        entry = "call task-entry.cmd"
        common = f"""你是员工机器上的 Codex Agent，正在领取经理机通过 inbox 下发的独立任务。
Windows 命令工具使用 shell=C:\\Windows\\System32\\cmd.exe、login=false。
当前工作目录已经是任务目录，保持此目录。使用下面的短命令，文件输入使用本目录文件名。
cmd 参数直接填写下面的 call 命令；shell 字段已选择解释器，不再嵌套 cmd.exe /c。
命令中的双引号为普通路径引号，不在命令字符串中写反斜杠转义引号。
本项目提供固定文件执行入口，直接调用以下命令读取任务并写领取回执：
{entry} claim
用文件编辑工具编写原生 JSON 文件，读取文本使用 cmd 的 type 命令。
执行业务时在本目录写 command.json，例如：
{{"args":["post-comment","--feed-id","实际笔记ID","--xsec-token","实际令牌","--content","最终评论"]}}
然后调用 {entry} invoke --input-file command.json
账号由固定入口从 task.json 注入。每一步使用不同输入文件名。
入口直接返回短业务输出，同时保存同名 .output.json、.stderr.txt、.execution.json。
业务状态以输出内容为准；exit_code=0 本身不代表业务成功。仅输出截断或需错误详情时读取文件。
命令帮助：{entry} help；子命令帮助：{entry} help --command reply-inbox collect。
合法参数示例：{{"args":["reply-inbox","collect","--limit","20"]}}，
{{"args":["reply-inbox","list","--state","NEW","--limit","20"]}}，
{{"args":["reply-inbox","context","--event-id","实际事件ID"]}}，
{{"args":["reply-inbox","apply","--decision-file","决策文件绝对路径"]}}。
固定本轮事件集合，数量及渠道以任务授权为准，读取上下文后再生成决策。
复用固定入口，不生成调用脚本，不使用内联 python -c，不为查调用方法搜索源码。
参数校验失败且未执行业务时用 help 核对参数；业务发送结果未知时停止并保留证据。
遇到代码问题时可写 bug.json，包含 summary、reproduction、expected、actual 和 error_code，
调用 {entry} report-bug --input-file bug.json 将证据存入本轮待回传目录；
QUEUED 表示本地提交成功，runner 结束时通过配置的 outbox 回传并保存发布回执。
故障回执保留原任务状态；代码修复由经理项目分支完成，员工安装经理下发的修复包。
claim 命令输出任务并生成 {root / "claimed.json"}，阅读输出后独立完成任务。
记忆上下文见 claim 返回的 memory_context_file；按治理、共享核心、账号长期、相关短期、
当前任务的顺序读取采用。task.json 保留完整原始任务，已读字段直接复用。
最终用文件编辑工具将原生 JSON 写入
{root / "execution-result.json"}，包含 state、message、business_result（对象）、
item_results（对象数组）、evidence_paths（路径字符串数组）。state 取值为 {RESULT_STATES}。
不要把业务对象或逐项数组再次编码为字符串；路径和评论原文交给 JSON 序列化处理。
运行 {entry} finish 校验回执后，最终只返回 {{"result_file":"execution-result.json"}}。
所有执行证据和文本保存在 {root}，采用绝对路径、UTF-8。
"""
        if task.get("capability") == PROBE_CAPABILITY:
            return (
                common
                + """本次是本地连接验证。只读取任务文件，写领取回执和本地结果。
将 parameters.probe_token 原样放入 business_result 的 probe_token，state=SUCCESS，
item_results=[]。不调用项目业务命令，不访问账号、浏览器或业务网络。
"""
            )
        return (
            common
            + f"""一次读取 {self.project / "AGENTS.md"}、{self.project / "SKILL.md"} 和
任务对应技能：评论读取 skills/xhs-interact/SKILL.md 和 skills/xhs-explore/SKILL.md，
回复读取 skills/xhs-reply/SKILL.md，指标读取 skills/xhs-content-ops/SKILL.md。
这些技能路径均相对于 {self.project}，读取后复用，不搜索文件位置。
使用本项目 Python CLI / ApplicationService 执行链，复用机器当前配置和账号环境。
使用指定 account_slot 本地已绑定的 UID 核对下发账号身份，绑定有效时直接开始业务。
本机槽位检查使用 {{"args":["account-doctor"]}}，身份与连接由项目执行链校验。
日常任务复用槽位绑定；首次绑定、用户手动换号或主动检查身份时才读取页面 UID。
身份不符、绑定缺失或换号未完成时返回 BLOCKED。
仅执行任务 capability、parameters 与 authorization 授权的范围和数量。
账号人设来自 persona_snapshot 或 parameters.persona；未填写时使用默认“友好评论者”。
阅读真实笔记和上下文后依据人设自主生成评论，不能用固定模板代替理解。
逐篇浏览随机停留 15–45 秒；点赞间隔 90–180 秒且穿插至少 2 篇不点赞的浏览。
如任务约定更长间隔，以任务为准。评论按任务指定节奏执行。
敏感文本只放在固定入口的 JSON args 中，由入口在进程内传给 cli.main。
每次外部写入前将目标与内容写入本地证据，再调用一次；不自动重试结果未知的外部写入。
评论发送后读取笔记评论，核对本账号 UID 和原文，保存平台回读证据。
每条评论在 item_results 中提供 status、success、feed_id、content、platform_readback、
evidence_path；只有平台回读确认的评论才写 SUCCESS 和 platform_readback=true。
结果未知写 RESULT_UNKNOWN。最终结果准确反映逐项结果与实际完成数量。
发布必须有用户对应内容的明确确认。保持源码、Chrome Profile、登录和配对状态。
不创建定时任务或子 Agent，任务完成后返回结构化结果并结束。
"""
        )
