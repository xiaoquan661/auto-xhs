from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_transport import ManagerTransportError
from service_errors import ServiceError


class FakeTransport:
    def __init__(self) -> None:
        self.responses: dict[str, list[dict | Exception]] = defaultdict(list)
        self.calls: list[tuple[str, dict]] = []
        self.uploads: list[tuple[str, str]] = []
        self.downloads: list[tuple[str, str]] = []
        self.download_responses: list[Exception | None] = []

    def queue(self, action: str, *responses: dict | Exception) -> None:
        self.responses[action].extend(responses)

    def queue_download(self, *responses: Exception | None) -> None:
        self.download_responses.extend(responses)

    def invoke(
        self,
        _machine: dict,
        action: str,
        payload: dict,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.calls.append((action, payload))
        queued = self.responses[action]
        if queued:
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if action == "health":
            return {
                "success": True,
                "health": {
                    "machine_id": "PC001",
                    "role": "executor",
                    "project_version": "0.1.0",
                    "healthy": True,
                    "paused": False,
                    "account_slots": [],
                },
            }
        if action == "accept-task":
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "ACCEPTED",
                },
            }
        if action == "run-task":
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "SUCCESS",
                    "project_version": "0.1.0",
                    "result_summary": "页面回读成功",
                },
            }
        if action in {"pause-task", "resume-task"}:
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "PAUSED" if action == "pause-task" else "ACCEPTED",
                },
            }
        if action in {"pause-machine", "resume-machine"}:
            return {"success": True, "machine": {"paused": action == "pause-machine"}}
        if action == "prepare-transfer":
            return {
                "success": True,
                "transfer": {
                    "transfer_id": payload["transfer_id"],
                    "state": "PREPARED",
                    "staging_path": r"C:\manager-worker\staging\transfer-1",
                },
            }
        if action == "finalize-transfer":
            return {
                "success": True,
                "transfer": {
                    "state": "SUCCESS",
                    "actual_version": "1.0",
                    "installed_path": r"C:\manager-worker\packages\EXPLORE-01\1.0",
                },
            }
        if action == "list-function-versions":
            return {"success": True, "functions": [], "versions": []}
        if action == "set-function-version-policy":
            return {
                "success": True,
                "version": {
                    "function_id": payload["function_id"],
                    "version": payload["version"],
                    "status": payload["status"],
                    "scope": payload.get("scope") or {},
                    "stable": bool(payload.get("set_as_stable")),
                },
            }
        if action == "collect-results":
            return {"success": True, "results": [], "next_sequence": 0}
        if action == "task-status":
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "RUNNING",
                },
            }
        raise AssertionError(f"unexpected action: {action}, timeout={timeout_seconds}")

    def upload(
        self,
        _machine: dict,
        source: str,
        remote_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.uploads.append((source, remote_path))
        return {"success": True, "timeout_seconds": timeout_seconds}

    def download(
        self,
        _machine: dict,
        remote_path: str,
        destination: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.downloads.append((remote_path, destination))
        if self.download_responses:
            response = self.download_responses.pop(0)
            if response is not None:
                raise response
        Path(destination).write_text("evidence", encoding="utf-8")
        return {"success": True, "timeout_seconds": timeout_seconds}


def machine_values(machine_id: str = "PC001", host: str = "192.168.1.21") -> dict:
    return {
        "manager_id": "MILA-MANAGER",
        "machine_id": machine_id,
        "ssh_host": host,
        "ssh_port": 22,
        "ssh_user": "EDY",
        "working_directory": r"C:\xiaohongshu-skills",
        "python_executable": r"C:\xiaohongshu-skills\.venv\Scripts\python.exe",
        "allowed_versions": ["0.1.0"],
    }


def task_request() -> dict:
    return {
        "task_id": "task-1",
        "machine_id": "PC001",
        "account_slot": "brand-a",
        "capability": "search-feeds",
        "request_summary": "搜索指定关键词",
        "parameters": {"keyword": "英语口语"},
        "authorization": {},
        "write_intent": False,
    }


def service(
    tmp_path: Path, transport: FakeTransport | None = None
) -> tuple[ManagerService, FakeTransport]:
    fake = transport or FakeTransport()
    return (
        ManagerService(store=ManagerStore(tmp_path / "manager"), transport=fake),  # type: ignore[arg-type]
        fake,
    )


def register_ready(manager: ManagerService) -> None:
    manager.register_machine(machine_values())
    checked = manager.check_machine("PC001")
    assert checked["status"] == "READY"


def test_register_machine_is_persistent_and_exact_duplicate_is_idempotent(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)

    first = manager.register_machine(machine_values())
    second = manager.register_machine(machine_values())
    reloaded = ManagerService(
        store=ManagerStore(tmp_path / "manager"),
        transport=FakeTransport(),  # type: ignore[arg-type]
    )

    assert first["status"] == "REGISTERED"
    assert first["machine"]["record_id"] == "machine:PC001"
    assert first["machine"]["manager_id"] == "MILA-MANAGER"
    assert first["machine"]["ssh_state"] == "UNVERIFIED"
    assert second["status"] == "ALREADY_REGISTERED"
    assert reloaded.list_machines()["machines"][0]["machine_id"] == "PC001"


def test_machine_id_is_normalized_across_registration_and_health(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    registered = manager.register_machine(machine_values(machine_id="executor-01"))
    transport.queue(
        "health",
        {
            "health": {
                "machine_id": "EXECUTOR-01",
                "role": "executor",
                "project_version": "0.1.0",
                "healthy": True,
            }
        },
    )

    checked = manager.check_machine("executor-01")

    assert registered["machine"]["machine_id"] == "EXECUTOR-01"
    assert checked["status"] == "READY"


def test_register_rejects_duplicate_id_or_endpoint(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.register_machine(machine_values())

    with pytest.raises(ServiceError) as duplicate_id:
        manager.register_machine(machine_values(host="192.168.1.22"))
    with pytest.raises(ServiceError) as duplicate_endpoint:
        manager.register_machine(machine_values(machine_id="PC002"))

    assert duplicate_id.value.code == "DUPLICATE_MACHINE_ID"
    assert duplicate_endpoint.value.code == "DUPLICATE_MACHINE_ENDPOINT"


def test_check_machine_validates_role_identity_version_and_offline(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "health",
        {
            "health": {
                "machine_id": "PC999",
                "role": "executor",
                "project_version": "0.1.0",
                "healthy": True,
            }
        },
        {
            "health": {
                "machine_id": "PC001",
                "role": "manager",
                "project_version": "0.1.0",
                "healthy": True,
            }
        },
        {
            "health": {
                "machine_id": "PC001",
                "role": "executor",
                "project_version": "2.0.0",
                "healthy": True,
            }
        },
        ManagerTransportError("OFFLINE", "连接失败"),
    )

    assert manager.check_machine("PC001")["machine"]["last_error_code"] == ("MACHINE_ID_MISMATCH")
    assert manager.check_machine("PC001")["machine"]["last_error_code"] == ("MACHINE_ROLE_MISMATCH")
    assert manager.check_machine("PC001")["machine"]["last_error_code"] == (
        "PROJECT_VERSION_MISMATCH"
    )
    assert manager.check_machine("PC001")["status"] == "OFFLINE"


def test_check_machine_reports_remote_pause_and_degraded_health(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "health",
        {
            "machine_id": "PC001",
            "role": "executor",
            "project_version": "0.1.0",
            "health": "PAUSED",
            "paused": True,
        },
        {
            "machine_id": "PC001",
            "role": "executor",
            "project_version": "0.1.0",
            "health": "DEGRADED",
            "paused": False,
        },
    )

    paused = manager.check_machine("PC001")
    degraded = manager.check_machine("PC001")

    assert paused["success"] is True
    assert paused["status"] == "PAUSED"
    assert degraded["success"] is False
    assert degraded["machine"]["last_error_code"] == "EXECUTOR_UNHEALTHY"


def test_offline_dispatch_stays_pending_and_is_not_business_failure(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("health", ManagerTransportError("OFFLINE", "连接超时"))

    result = manager.dispatch_task(task_request())

    assert result["status"] == "PENDING_DISPATCH"
    assert result["task"]["business_state"] == ""
    assert result["task"]["reconciliation_required"] is False


def test_dispatch_accepts_runs_and_records_business_readback(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)

    result = manager.dispatch_task(task_request())

    assert result["status"] == "SUCCESS"
    assert result["task"]["dispatch_state"] == "COMPLETED"
    assert result["task"]["business_state"] == "SUCCESS"
    assert result["task"]["state"] == "SUCCESS"
    assert result["task"]["actual_version"] == "0.1.0"
    assert result["task"]["message"] == "页面回读成功"
    assert result["task"]["result_summary"] == "页面回读成功"
    assert [action for action, _payload in transport.calls][-2:] == [
        "accept-task",
        "run-task",
    ]


def test_dispatch_sends_manager_scope_and_records_pinned_function_version(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    request = {
        **task_request(),
        "function_id": "EXPLORE-02",
        "allowed_function_versions": ["1.1", "1.0"],
    }
    transport.queue(
        "accept-task",
        {
            "success": True,
            "execution": {
                "remote_execution_id": "execution-versioned",
                "state": "ACCEPTED",
                "function_id": "EXPLORE-02",
                "project_version": "0.1.0",
                "actual_version": "1.1",
                "version_status": "TRIAL",
            },
        },
    )
    transport.queue(
        "run-task",
        {
            "success": True,
            "execution": {
                "remote_execution_id": "execution-versioned",
                "state": "SUCCESS",
                "function_id": "EXPLORE-02",
                "project_version": "0.1.0",
                "actual_version": "1.1",
                "version_status": "TRIAL",
                "result_summary": "页面回读成功",
            },
        },
    )

    result = manager.dispatch_task(request)

    envelope = next(payload for action, payload in transport.calls if action == "accept-task")
    assert envelope["manager_id"] == "MILA-MANAGER"
    assert envelope["function_id"] == "EXPLORE-02"
    assert envelope["allowed_function_versions"] == ["1.1", "1.0"]
    assert result["task"]["project_version"] == "0.1.0"
    assert result["task"]["actual_version"] == "1.1"
    assert result["task"]["version_status"] == "TRIAL"


def test_versioned_dispatch_requires_an_allowed_function_version(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.register_machine(machine_values())

    with pytest.raises(ServiceError) as invalid:
        manager.dispatch_task({**task_request(), "function_id": "EXPLORE-02"})

    assert invalid.value.code == "INVALID_TASK"


def test_repeated_completed_dispatch_does_not_create_or_run_again(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.dispatch_task(task_request())
    call_count = len(transport.calls)

    repeated = manager.dispatch_task(task_request())

    assert repeated["status"] == "IDEMPOTENT"
    assert len(transport.calls) == call_count
    assert len(manager.list_tasks()["tasks"]) == 1


def test_accept_timeout_requires_reconciliation_before_resend(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "accept-task",
        ManagerTransportError("RESULT_UNKNOWN", "响应超时", result_unknown=True),
    )

    first = manager.dispatch_task(task_request())
    second = manager.dispatch_task(task_request())

    assert first["status"] == "DISPATCH_UNKNOWN"
    assert second["status"] == "RECONCILIATION_REQUIRED"
    assert first["task"]["business_state"] == ""


def test_run_timeout_keeps_remote_execution_and_requires_reconciliation(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "run-task",
        ManagerTransportError("RESULT_UNKNOWN", "运行超时", result_unknown=True),
    )

    result = manager.dispatch_task(task_request())

    assert result["status"] == "RECONCILIATION_REQUIRED"
    assert result["task"]["remote_execution_id"] == "execution-1"
    assert result["task"]["business_state"] == ""


def test_reconcile_not_found_allows_safe_redispatch(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "accept-task",
        ManagerTransportError("RESULT_UNKNOWN", "响应超时", result_unknown=True),
    )
    manager.dispatch_task(task_request())
    transport.queue(
        "task-status",
        {
            "success": False,
            "error": {"code": "TASK_NOT_FOUND", "message": "未接收"},
        },
    )

    result = manager.reconcile_task("task-1")

    assert result["status"] == "PENDING_DISPATCH"
    assert result["task"]["reconciliation_required"] is False


def test_remote_worker_error_is_not_treated_as_accepted_task(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "accept-task",
        {
            "success": False,
            "error": {
                "code": "PROJECT_VERSION_NOT_ALLOWED",
                "message": "执行机版本不在允许范围",
            },
        },
    )

    result = manager.dispatch_task(task_request())

    assert result["status"] == "BLOCKED"
    assert result["task"]["business_state"] == ""
    assert result["task"]["error_code"] == "PROJECT_VERSION_NOT_ALLOWED"
    assert [action for action, _payload in transport.calls].count("run-task") == 0


def test_local_pending_task_pause_and_resume_are_idempotent(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("health", ManagerTransportError("OFFLINE", "离线"))
    manager.dispatch_task(task_request())

    paused = manager.pause_task("task-1")
    paused_again = manager.pause_task("task-1")
    call_count = len(transport.calls)
    repeated_dispatch = manager.dispatch_task(task_request())
    resumed = manager.resume_task("task-1")

    assert paused["status"] == "PAUSED"
    assert paused_again["status"] == "PAUSED"
    assert repeated_dispatch["status"] == "PAUSED"
    assert len(transport.calls) == call_count
    assert resumed["status"] == "PENDING_DISPATCH"


def test_task_authorization_rejects_private_credentials(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    request = task_request()
    request["authorization"] = {"approval_id": "approval-1", "session_token": "secret"}

    with pytest.raises(ServiceError) as error:
        manager.dispatch_task(request)

    assert error.value.code == "PRIVATE_CREDENTIAL_NOT_ALLOWED"


def test_machine_pause_is_remembered_while_offline_and_blocks_new_dispatch(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("pause-machine", ManagerTransportError("OFFLINE", "离线"))

    pause = manager.pause_machine("PC001")
    task = manager.dispatch_task(task_request())

    assert pause["status"] == "PAUSE_PENDING"
    assert pause["machine"]["desired_paused"] is True
    assert task["status"] == "PAUSED"


def test_function_package_transfer_prepares_uploads_finalizes_and_is_idempotent(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    package = tmp_path / "EXPLORE-01"
    package.mkdir()
    (package / "manifest.json").write_text('{"version":"1.0"}', encoding="utf-8")
    request = {
        "transfer_id": "transfer-1",
        "machine_id": "PC001",
        "transfer_type": "function_package",
        "source_path": str(package.resolve()),
        "allowed_versions": ["0.1.0"],
    }

    first = manager.transfer(request)
    call_count = len(transport.calls)
    second = manager.transfer(request)

    assert first["status"] == "SUCCESS"
    assert first["transfer"]["actual_version"] == "1.0"
    assert first["transfer"]["remote_path"].endswith(r"EXPLORE-01\1.0")
    assert transport.uploads[0][0] == str(package.resolve())
    assert second["status"] == "IDEMPOTENT"
    assert len(transport.calls) == call_count


def test_transfer_timeout_is_not_reported_as_installed(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    source = tmp_path / "task.json"
    source.write_text("{}", encoding="utf-8")
    transport.queue(
        "prepare-transfer",
        ManagerTransportError("RESULT_UNKNOWN", "准备超时", result_unknown=True),
    )

    result = manager.transfer(
        {
            "transfer_id": "transfer-2",
            "machine_id": "PC001",
            "transfer_type": "task_package",
            "source_path": str(source.resolve()),
        }
    )

    assert result["status"] == "RESULT_UNKNOWN"
    assert result["transfer"]["actual_version"] == ""


def test_transfer_retry_reconciles_remote_success_without_uploading_again(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    source = tmp_path / "task.json"
    source.write_text("{}", encoding="utf-8")
    request = {
        "transfer_id": "transfer-remote-success",
        "machine_id": "PC001",
        "transfer_type": "task_package",
        "source_path": str(source.resolve()),
    }
    transport.queue(
        "finalize-transfer",
        ManagerTransportError("RESULT_UNKNOWN", "回执超时", result_unknown=True),
    )

    first = manager.transfer(request)
    transport.queue(
        "prepare-transfer",
        {
            "success": True,
            "transfer": {
                "state": "SUCCESS",
                "actual_version": "transfer-remote-success",
                "installed_path": r"C:\worker\task-inbox\task\transfer-remote-success",
            },
        },
    )
    second = manager.transfer(request)

    assert first["status"] == "RESULT_UNKNOWN"
    assert second["status"] == "IDEMPOTENT"
    assert second["transfer"]["actual_version"] == "transfer-remote-success"
    assert len(transport.uploads) == 1


def test_remote_transfer_validation_failure_keeps_actual_version_empty(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    package = tmp_path / "bad-package"
    package.mkdir()
    transport.queue(
        "finalize-transfer",
        {
            "success": False,
            "transfer": {
                "state": "FAILED",
                "error_code": "MANIFEST_PROJECT_VERSION_MISMATCH",
                "message": "功能包与执行机版本不兼容",
            },
        },
    )

    result = manager.transfer(
        {
            "transfer_id": "transfer-rejected",
            "machine_id": "PC001",
            "transfer_type": "function_package",
            "source_path": str(package.resolve()),
        }
    )

    assert result["status"] == "FAILED"
    assert result["transfer"]["actual_version"] == ""
    assert result["transfer"]["error_code"] == "MANIFEST_PROJECT_VERSION_MISMATCH"


def test_version_list_refreshes_cache_and_offline_returns_last_known(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    remote_version = {
        "function_id": "EXPLORE-02",
        "version": "1.1",
        "status": "TRIAL",
        "scope": {
            "manager_ids": ["MILA-MANAGER"],
            "machine_ids": ["PC001"],
            "account_slots": ["brand-a"],
            "allowed_actions": ["search-feeds"],
        },
        "stable": False,
    }
    transport.queue(
        "list-function-versions",
        {
            "success": True,
            "functions": [{"function_id": "EXPLORE-02", "stable_version": "1.0"}],
            "versions": [remote_version],
        },
        ManagerTransportError("OFFLINE", "执行机离线"),
    )

    refreshed = manager.list_function_versions("PC001", "EXPLORE-02")
    offline = manager.list_function_versions("PC001", "EXPLORE-02")

    assert refreshed["source"] == "REMOTE"
    assert refreshed["versions"][0]["status"] == "TRIAL"
    assert offline["status"] == "OFFLINE"
    assert offline["source"] == "LAST_KNOWN"
    assert offline["versions"][0]["version"] == "1.1"


def test_set_function_version_policy_sends_explicit_scope_and_caches_result(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    scope = {
        "manager_ids": ["MILA-MANAGER"],
        "machine_ids": ["PC001"],
        "account_slots": ["brand-a"],
        "allowed_actions": ["search-feeds"],
    }
    transport.queue(
        "set-function-version-policy",
        {
            "success": True,
            "version": {
                "function_id": "EXPLORE-02",
                "version": "1.0",
                "status": "FORMAL",
                "scope": scope,
                "stable": True,
            },
        },
    )

    result = manager.set_function_version_policy(
        {
            "machine_id": "PC001",
            "function_id": "EXPLORE-02",
            "version": "1.0",
            "status": "正式",
            "scope": scope,
            "set_as_stable": True,
        }
    )

    action, payload = transport.calls[-1]
    assert action == "set-function-version-policy"
    assert payload["status"] == "FORMAL"
    assert payload["scope"] == scope
    assert result["version"]["stable"] is True
    cached = manager.store.list("versions")
    assert cached[0]["function_id"] == "EXPLORE-02"


def test_collect_results_is_incremental_idempotent_and_downloads_evidence(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.dispatch_task(task_request())
    result_item = {
        "sequence": 1,
        "result_revision": 1,
        "task_id": "task-1",
        "remote_execution_id": "execution-1",
        "state": "SUCCESS",
        "result_summary": "页面已回读",
        "error_classification": "",
        "manual_action_required": False,
        "item_results": [
            {"target_id": "note-1", "state": "SUCCESS"},
            {
                "target_id": "note-2",
                "state": "BLOCKED",
                "error_classification": "ACCOUNT_REVIEW",
            },
        ],
        "evidence_index": [
            {
                "remote_path": r"C:\worker\evidence\readback.json",
                "relative_path": "readback.json",
            }
        ],
        "incremental_data": [{"record_type": "operation", "record_id": "event-1"}],
    }
    transport.queue(
        "collect-results",
        {"success": True, "results": [result_item], "next_sequence": 1},
        {"success": True, "results": [result_item], "next_sequence": 1},
    )

    first = manager.collect_results("PC001")
    second = manager.collect_results("PC001")

    assert first["collected"] == 1
    assert first["next_cursor"] == 1
    assert first["state_counts"] == {"SUCCESS": 1}
    assert first["summary"]["item_state_counts"] == {"BLOCKED": 1, "SUCCESS": 1}
    assert first["summary"]["error_class_counts"] == {"ACCOUNT_REVIEW": 1}
    assert first["summary"]["incremental_record_count"] == 1
    assert len(transport.downloads) == 1
    assert second["collected"] == 0
    assert second["duplicates"] == 1
    assert manager.get_task("task-1")["task"]["message"] == "页面已回读"

    stored = manager.list_results("PC001")
    assert len(stored["results"]) == 1
    assert stored["results"][0]["record_id"].startswith("result:PC001:")
    assert stored["results"][0]["data_status"] == "observed"
    assert stored["summary"]["item_results_status"] == "observed"


def test_collect_offline_preserves_previous_cursor(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.store.set_collection_cursor("PC001", 7)
    transport.queue("collect-results", ManagerTransportError("OFFLINE", "离线"))

    result = manager.collect_results("PC001")

    assert result["status"] == "OFFLINE"
    assert result["cursor"] == 7
    assert manager.store.get_collection_cursor("PC001") == 7


def test_incremental_records_deduplicate_natural_key_across_result_revisions(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    common = {
        "task_id": "task-data",
        "remote_execution_id": "execution-data",
        "state": "SUCCESS",
        "item_results_status": "missing",
        "item_results_missing_reason": "本任务没有逐项结果",
        "incremental_data_status": "observed",
        "incremental_data": [{"record_type": "operation", "record_id": "event-1", "value": 1}],
    }
    transport.queue(
        "collect-results",
        {
            "success": True,
            "results": [
                {**common, "sequence": 1, "result_revision": 1},
                {**common, "sequence": 2, "result_revision": 2},
            ],
            "next_sequence": 2,
        },
    )

    result = manager.collect_results("PC001", download_evidence=False)

    assert result["collected"] == 2
    assert result["incremental_collected"] == 1
    assert result["incremental_duplicates"] == 1
    stored = manager.list_results("PC001")
    assert stored["incremental_unique_count"] == 1
    assert stored["incremental_records"][0]["record_id"] == "event-1"


def test_evidence_download_retries_after_structured_cursor_advances(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    result_item = {
        "sequence": 1,
        "result_revision": 1,
        "task_id": "task-evidence",
        "remote_execution_id": "execution-evidence",
        "state": "SUCCESS",
        "item_results": [],
        "item_results_status": "observed",
        "incremental_data": [],
        "incremental_data_status": "observed",
        "evidence_index": [
            {
                "remote_path": r"C:\worker\evidence\readback.json",
                "relative_path": "readback.json",
            }
        ],
    }
    transport.queue(
        "collect-results",
        {"success": True, "results": [result_item], "next_sequence": 1},
        {"success": True, "results": [], "next_sequence": 1},
    )
    transport.queue_download(ManagerTransportError("OFFLINE", "证据下载离线"), None)

    first = manager.collect_results("PC001")
    second = manager.collect_results("PC001")

    assert first["next_cursor"] == 1
    assert first["pending_evidence"] == 1
    assert first["status"] == "PARTIAL_SUCCESS"
    assert second["next_cursor"] == 1
    assert second["pending_evidence"] == 0
    assert second["status"] == "SUCCESS"
    assert len(transport.downloads) == 2


def test_collect_remote_error_preserves_previous_cursor(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.store.set_collection_cursor("PC001", 4)
    transport.queue(
        "collect-results",
        {
            "success": False,
            "error": {"code": "STATE_UNREADABLE", "message": "结果状态无法读取"},
        },
    )

    result = manager.collect_results("PC001")

    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "STATE_UNREADABLE"
    assert manager.store.get_collection_cursor("PC001") == 4


def test_evidence_relative_path_stays_inside_revision_directory(tmp_path: Path) -> None:
    root = tmp_path / "revision"
    parts = ManagerService._safe_evidence_parts(
        r"C:\..\..\outside\readback.json",
        fallback="evidence-1",
    )

    target = root.joinpath(*parts).resolve()

    assert target.is_relative_to(root.resolve())
    assert ".." not in parts


def test_result_summary_does_not_turn_missing_data_into_observed_zero() -> None:
    summary = ManagerService._summarize_results(
        [
            {
                "task_id": "missing",
                "state": "SUCCESS",
                "item_results": [],
                "item_results_status": "missing",
                "item_results_missing_reason": "平台没有提供逐项数据",
                "incremental_data": [],
                "incremental_data_status": "missing",
                "incremental_data_missing_reason": "本能力没有增量数据源",
            },
            {
                "task_id": "observed-empty",
                "state": "SUCCESS",
                "item_results": [],
                "item_results_status": "observed",
                "incremental_data": [],
                "incremental_data_status": "observed",
            },
        ]
    )

    assert summary["item_results_status"] == "mixed"
    assert summary["item_results_data_status_counts"] == {"missing": 1, "observed": 1}
    assert summary["incremental_data_status"] == "mixed"
    assert summary["incremental_record_count"] == 0
    assert {gap["task_id"] for gap in summary["data_gaps"]} == {"missing"}
