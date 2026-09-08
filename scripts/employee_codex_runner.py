"""Run an inbox task with the employee's installed Codex and persist its receipts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

try:
    from .capability_registry import RiskLevel, get_capability_policy
except ImportError:
    from capability_registry import RiskLevel, get_capability_policy


PROBE_CAPABILITY = "employee-agent-probe"
RESULT_STATES = ["SUCCESS", "PARTIAL_SUCCESS", "FAILED", "BLOCKED", "RESULT_UNKNOWN"]
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "state": {"type": "string", "enum": RESULT_STATES},
        "message": {"type": "string"},
        "business_result_json": {"type": "string"},
        "item_results_json": {"type": "string"},
        "evidence_paths": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["state", "message", "business_result_json", "item_results_json", "evidence_paths"],
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
    ) -> None:
        self.root = Path(state_root).resolve()
        self.project = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.on_claim = on_claim
        self.should_stop = should_stop

    def __call__(self, task: dict[str, Any]) -> dict[str, Any]:
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
        prompt = self._prompt(task, run_root)
        (run_root / "instructions.md").write_text(prompt, encoding="utf-8")
        command = [
            executable,
            "exec",
            "--json",
            "--skip-git-repo-check",
            "-C",
            str(self.project),
            "--sandbox",
            "workspace-write",
            "--add-dir",
            str(self.root),
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
        try:
            with (run_root / "events.jsonl").open("w", encoding="utf-8") as events:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE,
                    stdout=events,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    cwd=self.project,
                    env={**os.environ, "PYTHONUTF8": "1"},
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
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
                    if self.should_stop and self.should_stop():
                        self._stop(process)
                        return self._incomplete(task, "员工 Agent 已停止，执行结果待核对", run_root)
                    if time.monotonic() >= deadline:
                        self._stop(process)
                        return self._incomplete(
                            task, "员工 Agent 执行超时，执行结果待核对", run_root
                        )
                    time.sleep(1)
            if process.returncode != 0 or not claimed or not result_path.exists():
                return self._incomplete(task, "员工 Agent 未返回完整的领取回执和结果", run_root)
            result = json.loads(result_path.read_text(encoding="utf-8-sig"))
            business = json.loads(result["business_result_json"])
            items = json.loads(result["item_results_json"])
            if result["state"] not in RESULT_STATES or not isinstance(business, dict):
                raise ValueError("结果状态或业务数据格式错误")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise ValueError("逐项执行结果格式错误")
            if capability == PROBE_CAPABILITY and business.get("probe_token") != (
                task.get("parameters") or {}
            ).get("probe_token"):
                raise ValueError("连接验证回执不匹配")
            if capability == "post-comment":
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

    def _prompt(self, task: dict[str, Any], root: Path) -> str:
        common = f"""你是员工机器上的 Codex Agent，正在领取经理机通过 inbox 下发的独立任务。
Windows 命令工具使用 shell=C:\\Windows\\System32\\cmd.exe、login=false。
文件读写使用 {self.project / ".venv" / "Scripts" / "python.exe"} 执行 Python 脚本。
读取 {root / "task.json"}。先在 {root / "claimed.json"} 写入 JSON：
task_id（必须与任务一致）、account_slot、claimed_at（当前时间）、summary（你理解的任务）。
该文件由你实际读取任务后写入，随后独立完成任务；最终按给定 schema 返回结果。
business_result_json 是 JSON 对象字符串，item_results_json 是 JSON 数组字符串。
所有执行证据、临时脚本和文本保存在 {root}，采用绝对路径、UTF-8。
"""
        if task.get("capability") == PROBE_CAPABILITY:
            return (
                common
                + """本次是本地连接验证。只读取任务文件，写领取回执和本地结果。
将 parameters.probe_token 原样放入 business_result_json 的 probe_token，state=SUCCESS，
item_results_json=[]。不调用项目业务命令，不访问账号、浏览器或业务网络。
"""
            )
        return (
            common
            + f"""在 {self.project} 阅读 AGENTS.md、SKILL.md 和任务对应技能后执行。
使用本项目 Python CLI / ApplicationService 执行链，复用机器当前配置和账号环境。
先核对指定 account_slot 的真实 UID 与下发的账号身份；身份不符或资料不足时返回 BLOCKED。
仅执行任务 capability、parameters 与 authorization 授权的范围和数量。
账号人设来自 persona_snapshot 或 parameters.persona；评论任务缺少人设时返回 BLOCKED。
阅读真实笔记和上下文后依据人设自主生成评论，不能用固定模板代替理解。
逐篇浏览随机停留 15–45 秒；点赞间隔 90–180 秒且穿插至少 2 篇不点赞的浏览。
如任务约定更长间隔，以任务为准。评论按任务指定节奏执行。
敏感文本通过文件传递；若 CLI 没有文本文件参数，可在本次证据目录创建 Python helper，
读取 JSON 文件后设置 sys.argv 调用 cli.main，不把评论文本内联到 shell 命令。
每次外部写入前将目标与内容写入本地证据，再调用一次；不自动重试结果未知的外部写入。
评论发送后读取笔记评论，核对本账号 UID 和原文，保存平台回读证据。
每条评论在 item_results_json 中提供 status、success、feed_id、content、platform_readback、
evidence_path；只有平台回读确认的评论才写 SUCCESS 和 platform_readback=true。
结果未知写 RESULT_UNKNOWN。最终结果准确反映逐项结果与实际完成数量。
发布必须有用户对应内容的明确确认。保持源码、Chrome Profile、登录和配对状态。
不创建定时任务或子 Agent，任务完成后返回结构化结果并结束。
"""
        )
