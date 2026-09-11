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


def code_path(root: Path, relative: str) -> Path:
    """Limit a code repair to project sources and tests, preserving runtime data."""
    path = Path(relative)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parts
        or path.parts[0] not in {"scripts", "tests", "webui", "extension", "skills"}
    ):
        raise ServiceError(
            "INVALID_REPAIR_PATH", "修复文件必须位于源码、测试、WebUI、扩展或技能目录"
        )
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
