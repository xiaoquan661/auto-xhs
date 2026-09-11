"""执行机固定 JSON stdin/stdout 入口。"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any, TextIO

try:
    from .manager_worker_service import ManagerWorkerService, WorkerServiceError
    from .service_errors import ServiceError
except ImportError:
    from manager_worker_service import ManagerWorkerService, WorkerServiceError
    from service_errors import ServiceError


def _action_payload(request: dict[str, Any]) -> dict[str, Any]:
    payload = request.get("payload")
    if payload is not None and not isinstance(payload, dict):
        raise WorkerServiceError("INVALID_REQUEST", "payload必须是JSON对象")
    result = dict(payload or {})
    result.update(
        {key: value for key, value in request.items() if key not in {"action", "payload"}}
    )
    return result


def dispatch_request(
    service: ManagerWorkerService,
    request: dict[str, Any],
) -> dict[str, Any]:
    """把一个协议请求路由到固定动作。"""

    action = str(request.get("action") or "").strip().lower()
    if not action:
        raise WorkerServiceError("INVALID_REQUEST", "action不能为空")
    payload = _action_payload(request)
    if action in {"debug-report", "debug-apply", "debug-status", "debug-rollback"}:
        return service.debug_action(action.removeprefix("debug-"), payload)
    if action in {"memory-save", "memory-search", "memory-recall"}:
        return service.memory_action(action.removeprefix("memory-"), payload)
    if action == "health":
        return service.health()
    if action == "list-accounts":
        return service.list_accounts()
    if action == "account-status":
        return service.account_status(str(payload.get("account") or ""))
    if action == "account-doctor":
        return service.account_doctor(str(payload.get("account") or ""))
    if action == "account-action":
        return service.account_action(
            str(payload.get("account") or ""),
            str(payload.get("account_action") or ""),
        )
    if action == "account-lifecycle":
        return service.account_lifecycle(payload)
    if action == "account-field-request":
        return service.request_account_field_action(
            str(payload.get("account") or ""),
            payload,
        )
    if action == "account-management":
        return service.update_account_management(
            str(payload.get("account") or ""),
            payload,
        )
    if action == "executor-status":
        return service.executor_status()
    if action == "start-executor":
        return service.start_executor_service()
    if action == "restart-executor":
        return service.restart_executor_service()
    if action == "webui-status":
        return service.webui_status()
    if action == "start-webui":
        return service.start_webui()
    if action == "restart-webui":
        return service.restart_webui()
    if action == "configure":
        return service.configure(payload)
    if action == "list-function-versions":
        return service.list_function_versions(payload)
    if action == "set-function-version-policy":
        return service.set_function_version_policy(payload)
    if action == "accept-task":
        return service.accept_task(payload)
    if action == "wake-task":
        return service.wake_task(
            task_id=payload.get("task_id"),
            file_name=payload.get("file_name"),
        )
    if action == "start-task":
        return service.start_task(
            task_id=payload.get("task_id"),
            remote_execution_id=payload.get("remote_execution_id"),
        )
    if action == "run-task":
        return service.run_task(
            task_id=payload.get("task_id"),
            remote_execution_id=payload.get("remote_execution_id"),
        )
    if action == "task-status":
        return service.task_status(
            task_id=payload.get("task_id"),
            remote_execution_id=payload.get("remote_execution_id"),
        )
    if action == "pause-task":
        return service.pause_task(
            task_id=payload.get("task_id"),
            remote_execution_id=payload.get("remote_execution_id"),
        )
    if action == "resume-task":
        return service.resume_task(
            task_id=payload.get("task_id"),
            remote_execution_id=payload.get("remote_execution_id"),
        )
    if action == "pause-machine":
        return service.pause_machine()
    if action == "resume-machine":
        return service.resume_machine()
    if action == "prepare-transfer":
        return service.prepare_transfer(payload)
    if action == "finalize-transfer":
        return service.finalize_transfer(payload)
    if action == "collect-results":
        return service.collect_results(
            int(payload.get("since_sequence", payload.get("after_sequence", 0))),
            limit=int(payload.get("limit", 100)),
        )
    raise WorkerServiceError("UNKNOWN_ACTION", f"未知执行机动作：{action}")


def main(
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    service_factory: Callable[[], ManagerWorkerService] = ManagerWorkerService,
) -> int:
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    if hasattr(input_stream, "reconfigure"):
        input_stream.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(output_stream, "reconfigure"):
        output_stream.reconfigure(encoding="utf-8", errors="strict")
    try:
        raw = input_stream.read()
        if not raw.strip():
            raise WorkerServiceError("INVALID_REQUEST", "标准输入缺少JSON请求")
        request = json.loads(raw)
        if not isinstance(request, dict):
            raise WorkerServiceError("INVALID_REQUEST", "请求必须是JSON对象")
        result = dispatch_request(service_factory(), request)
        exit_code = 0
    except json.JSONDecodeError:
        result = WorkerServiceError("INVALID_JSON", "标准输入不是有效JSON").to_dict()
        exit_code = 0
    except (WorkerServiceError, ServiceError) as exc:
        result = exc.to_dict()
        exit_code = 0
    except Exception:
        result = WorkerServiceError("INTERNAL_ERROR", "执行机入口处理请求失败").to_dict()
        exit_code = 0
    output_stream.write(json.dumps(result, ensure_ascii=False) + "\n")
    output_stream.flush()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
