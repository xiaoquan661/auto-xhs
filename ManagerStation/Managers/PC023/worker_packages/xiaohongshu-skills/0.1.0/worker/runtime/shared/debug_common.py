"""Shared incident records and repair package validation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

from service_errors import ServiceError


def now() -> str:
    return datetime.now(UTC).isoformat()


def identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def source_layout(root: Path) -> dict:
    """Read the source layout of the project being repaired."""
    path = root / "source-layout.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def repair_relative_path(root: Path, relative: str) -> str:
    """Keep repair file names stable across deployed source layouts."""
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ServiceError("INVALID_REPAIR_PATH", "修复文件必须使用项目源码相对路径")
    if path.parts[0] in {"scripts", "tests", "webui", "extension", "skills", "prompts"}:
        return path.as_posix()
    layout = source_layout(root)
    for name, mapped in layout.get("module_files", {}).items():
        if Path(mapped) == path:
            suffix = "/__init__.py" if path.name == "__init__.py" else ".py"
            return "scripts/" + name.replace(".", "/") + suffix
    for name, mapped in layout.get("powershell_files", {}).items():
        if Path(mapped) == path:
            return "scripts/" + name
    webui = root / "webui-layout.json"
    if webui.is_file():
        assets = json.loads(webui.read_text(encoding="utf-8"))["assets"]
        for name, mapped in assets.items():
            if Path(mapped) == path:
                return "webui/" + name
    for name in ("extension", "skills", "prompts"):
        mapped = layout.get("resource_roots", {}).get(name)
        if mapped and path.is_relative_to(Path(mapped)):
            return (Path(name) / path.relative_to(mapped)).as_posix()
    raise ServiceError(
        "INVALID_REPAIR_PATH", "修复文件必须位于登记的源码、测试、WebUI、扩展、技能或提示模板目录"
    )


def code_path(root: Path, relative: str) -> Path:
    """Resolve a repair's logical name to this project's authoritative source."""
    logical = repair_relative_path(root, relative)
    path = Path(logical)
    layout = source_layout(root)
    if path.parts[0] == "scripts" and path.suffix == ".py":
        parts = list(path.parts[1:])
        parts[-1] = path.stem
        if parts[-1] == "__init__":
            parts.pop()
        mapped = layout.get("module_files", {}).get(".".join(parts))
        if mapped:
            path = Path(mapped)
    elif path.parts[0] == "scripts" and path.suffix == ".ps1":
        mapped = layout.get("powershell_files", {}).get(Path(*path.parts[1:]).as_posix())
        if mapped:
            path = Path(mapped)
    elif path.parts[0] == "webui" and (root / "webui-layout.json").is_file():
        assets = json.loads((root / "webui-layout.json").read_text(encoding="utf-8"))["assets"]
        mapped = assets.get(Path(*path.parts[1:]).as_posix())
        if mapped:
            path = Path(mapped)
    elif path.parts[0] in {"extension", "skills", "prompts"}:
        mapped = layout.get("resource_roots", {}).get(path.parts[0])
        if mapped:
            path = Path(mapped).joinpath(*path.parts[1:])
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ServiceError("INVALID_REPAIR_PATH", "修复文件超出项目目录")
    return resolved


def run_tests(root: Path, tests: list[str], output_root: Path) -> dict:
    if not tests or not all(isinstance(test, str) for test in tests):
        raise ServiceError("REPAIR_TESTS_REQUIRED", "请指定用于复现和回归的 pytest 测试")
    for test in tests:
        path = code_path(root, test.split("::", 1)[0])
        if not path.is_file() or Path(test).parts[0] != "tests":
            raise ServiceError("INVALID_REPAIR_TEST", "修复测试必须是 tests 目录内的现有测试")
    output_root.mkdir(parents=True, exist_ok=True)
    log = output_root / "pytest.log"
    started = now()
    command = [sys.executable, "-m", "pytest", *tests, "-q"]
    try:
        with log.open("w", encoding="utf-8") as stream:
            completed = subprocess.run(
                command,
                cwd=root,
                stdout=stream,
                stderr=subprocess.STDOUT,
                timeout=300,
                check=False,
            )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired:
        exit_code = 124
    report = {
        "passed": exit_code == 0,
        "exit_code": exit_code,
        "tests": tests,
        "started_at": started,
        "finished_at": now(),
        "log_path": str(log),
        "python": sys.executable,
    }
    write_json(output_root / "validation.json", report)
    return report


def incident_from_result(machine_id: str, result: dict) -> dict | None:
    status = str(result.get("state") or result.get("status") or "").upper()
    if status not in {"FAILED", "BLOCKED", "RESULT_UNKNOWN", "PARTIAL_SUCCESS"}:
        return None
    error_code = str(result.get("error_code") or "")
    if status == "PARTIAL_SUCCESS" and not error_code:
        return None
    account = str(result.get("account_slot") or "")
    execution = str(result.get("remote_execution_id") or result.get("task_id") or "")
    if not execution:
        return None
    category = "code"
    if status == "RESULT_UNKNOWN" or error_code in {"RESULT_UNKNOWN", "RECONCILIATION_REQUIRED"}:
        category = "reconciliation"
    elif status == "BLOCKED":
        category = "operation"
    return {
        "source_key": f"{machine_id}:{account}:{execution}:{error_code or status}",
        "machine_id": machine_id,
        "account_slot": account,
        "task_id": str(result.get("task_id") or ""),
        "remote_execution_id": execution,
        "source_result_revision": result.get("result_revision") or result.get("sequence"),
        "category": category,
        "error_code": error_code,
        "source_status": status,
        "summary": str(
            result.get("message") or result.get("result_summary") or error_code or status
        ),
        "project_version": str(result.get("project_version") or ""),
        "evidence_index": result.get("evidence_index") or {},
        "reconciliation_required": category == "reconciliation",
    }


def normalize_incident(payload: dict, machine_id: str) -> dict:
    summary = str(payload.get("summary") or "").strip()
    if not summary:
        raise ServiceError("INCIDENT_SUMMARY_REQUIRED", "请填写问题现象")
    category = str(payload.get("category") or "code")
    if category not in {"code", "operation", "reconciliation"}:
        raise ServiceError("INVALID_INCIDENT_CATEGORY", "问题类型必须为代码、运行环境或结果核对")
    fields = (
        "account_slot",
        "task_id",
        "remote_execution_id",
        "error_code",
        "source_status",
        "project_version",
        "source_result_revision",
        "evidence_index",
        "reproduction",
        "expected",
        "actual",
        "source_key",
        "reconciliation_required",
    )
    record = {key: payload[key] for key in fields if key in payload}
    return {
        **record,
        "incident_id": identifier("DBG"),
        "machine_id": machine_id,
        "category": category,
        "summary": summary,
        "state": "OPEN",
        "created_at": now(),
        "updated_at": now(),
        "history": [],
        "reconciliation_required": bool(payload.get("reconciliation_required"))
        or category == "reconciliation",
    }
