from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import manager_worker_service as worker_module
import pytest
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_worker import dispatch_request, main
from manager_worker_service import ManagerWorkerService, WorkerServiceError

PROJECT_VERSION = "1.5.0"


def make_service(
    root: Path,
    *,
    executor=None,
    local_calls: list[tuple[str, str | None]] | None = None,
    project_version: str = PROJECT_VERSION,
    process_starter=None,
) -> ManagerWorkerService:
    calls = local_calls if local_calls is not None else []

    def local_control(action: str, local_task_id: str | None) -> dict:
        calls.append((action, local_task_id))
        return {"success": True, "action": action}

    service = ManagerWorkerService(
        root,
        executor=executor,
        account_summary_reader=lambda: [
            {
                "name": "alpha",
                "platform": "xiaohongshu",
                "profile_mode": "existing",
                "paired": True,
            }
        ],
        local_control=local_control,
        process_starter=process_starter or subprocess.Popen,
    )
    service.configure(
        machine_id="PC001",
        role="executor",
        project_version=project_version,
        package_root=str(root / "installed"),
        task_inbox_root=str(root / "inbox"),
    )
    return service


def task_request(
    *,
    task_id: str = "task-1",
    dispatch_id: str = "dispatch-1",
) -> dict:
    return {
        "task_id": task_id,
        "dispatch_id": dispatch_id,
        "target_machine_id": "PC001",
        "allowed_versions": [PROJECT_VERSION],
        "task": {
            "source": "manager",
            "account_slot": "alpha",
            "capability": "search-feeds",
            "request_summary": "搜索露营",
            "parameters": {"keyword": "露营"},
        },
    }


def manifest(
    package_type: str,
    package_name: str,
    version: str,
    *,
    allowed_versions: list[str] | None = None,
) -> dict:
    value = {
        "schema_version": 1,
        "package_type": package_type,
        "package_name": package_name,
        "version": version,
        "allowed_versions": allowed_versions or [PROJECT_VERSION],
    }
    if package_type == "function_package":
        value.update(
            {
                "business_module": "小红书自动化项目/测试模块",
                "function_id": package_name,
                "task_code": package_name,
                "function_name": f"{package_name} 测试功能",
                "applicable_layer": "executor",
                "execution_mode": "local_webui",
                "supported_environment": ["Windows", "Python 3.12"],
                "dependencies": ["xiaohongshu-skills"],
                "call_relationships": ["manager -> worker -> application_service"],
                "entrypoint": "runtime/run.py",
                "rollback_version": version,
            }
        )
    return value


def write_function_package_files(root: Path, *, marker: str = "ok") -> None:
    (root / "config").mkdir(exist_ok=True)
    (root / "config" / "example.json").write_text("{}\n", encoding="utf-8")
    (root / "runtime").mkdir(exist_ok=True)
    (root / "runtime" / "run.py").write_text(f"print({marker!r})\n", encoding="utf-8")
    (root / "tests").mkdir(exist_ok=True)
    (root / "tests" / "history_replay.json").write_text("[]\n", encoding="utf-8")
    (root / "tests" / "test_package.py").write_text(
        "def test_package():\n    pass\n",
        encoding="utf-8",
    )
    for name in ("README.md", "CHANGELOG.md", "ROLLBACK.md"):
        (root / name).write_text(f"# {name}\n", encoding="utf-8")
    for name in ("RUN_RESULT.json", "VERIFICATION_REPORT.md"):
        suffix = "{}\n" if name.endswith(".json") else f"# {name}\n"
        (root / name).write_text(suffix, encoding="utf-8")


def install_function_version(
    service: ManagerWorkerService,
    *,
    transfer_id: str,
    function_id: str,
    version: str,
    project_version: str = PROJECT_VERSION,
) -> dict:
    prepared = service.prepare_transfer(
        transfer_id=transfer_id,
        package_type="function_package",
        package_name=function_id,
        version=version,
    )
    staging = Path(prepared["transfer"]["staging_path"])
    staging.joinpath("manifest.json").write_text(
        json.dumps(
            manifest(
                "function_package",
                function_id,
                version,
                allowed_versions=[project_version],
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_function_package_files(staging, marker=version)
    return service.finalize_transfer(transfer_id)


def test_config_health_and_state_are_json_runlock_backed(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    state_path = service.root / "worker-state.json"
    before = state_path.read_text(encoding="utf-8")

    health = service.health()

    assert health == {
        "success": True,
        "health": {
            "machine_id": "PC001",
            "role": "executor",
            "project_version": PROJECT_VERSION,
            "paused": False,
            "healthy": True,
            "status": "READY",
            "executor_service": {
                "ready": True,
                "url": "http://127.0.0.1:8766/api/v1",
                "error": "",
            },
            "local_service": {
                "ready": True,
                "url": "http://127.0.0.1:8766/api/v1",
                "error": "",
            },
            "account_slots": {
                "count": 1,
                "items": [
                    {
                        "name": "alpha",
                        "platform": "xiaohongshu",
                        "profile_mode": "existing",
                        "paired": True,
                    }
                ],
                "error": "",
            },
            "task_exchange": {
                "transport": "sftp",
                "inbox_root": str((service.root / "inbox").resolve()),
                "outbox_root": str((service.root / "task-outbox").resolve()),
            },
        },
    }
    assert state_path.read_text(encoding="utf-8") == before
    assert not (service.root / "worker-state.lock").exists()
    stored = json.loads(before)
    assert Path(stored["config"]["package_root"]).is_absolute()


def test_outbox_index_write_retries_transient_access_denied(tmp_path, monkeypatch) -> None:
    original_replace = worker_module.os.replace
    index_attempts = 0

    def replace_with_busy_index(source: str | Path, destination: str | Path) -> None:
        nonlocal index_attempts
        if Path(destination).name == "index.json":
            index_attempts += 1
            if index_attempts == 1:
                raise PermissionError("index temporarily busy")
        original_replace(source, destination)

    monkeypatch.setattr(worker_module.os, "replace", replace_with_busy_index)
    index_path = tmp_path / "index.json"
    worker_module._write_json_atomic(index_path, {"records": [{"record_type": "result"}]})
    index = json.loads(index_path.read_text(encoding="utf-8"))

    assert index_attempts == 2
    assert [record["record_type"] for record in index["records"]] == ["result"]


def test_worker_dashboard_snapshot_is_local_and_read_only(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    accepted = service.accept_task(task_request())
    state_path = service.root / "worker-state.json"
    before = state_path.read_text(encoding="utf-8")

    snapshot = service.dashboard_snapshot()

    assert snapshot["machine"]["machine_id"] == "PC001"
    assert snapshot["machine"]["manager_connection"] == "PENDING"
    assert snapshot["current_execution"]["task_id"] == "task-1"
    assert (
        snapshot["current_execution"]["remote_execution_id"]
        == accepted["execution"]["remote_execution_id"]
    )
    assert snapshot["agent_messages"][0]["message_type"] == "TASK_ACK"
    assert snapshot["exchange"]["pending_inbox"] == 0
    assert snapshot["outbox_records"] == []
    assert state_path.read_text(encoding="utf-8") == before


def test_worker_dashboard_groups_transport_receipts_by_execution(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    records = [
        {
            "task_id": "task-1",
            "remote_execution_id": "exec-1",
            "record_type": "acknowledgement",
            "sequence": 1,
            "recorded_at": "2026-09-18T00:00:00+00:00",
        },
        {
            "task_id": "task-1",
            "remote_execution_id": "exec-1",
            "record_type": "event",
            "sequence": 2,
            "recorded_at": "2026-09-18T00:00:01+00:00",
        },
        {
            "task_id": "task-1",
            "remote_execution_id": "exec-1",
            "record_type": "result",
            "sequence": 3,
            "recorded_at": "2026-09-18T00:00:02+00:00",
        },
    ]
    summaries = service._summarize_outbox_records(
        records,
        [
            {
                "task_id": "task-1",
                "remote_execution_id": "exec-1",
                "state": "SUCCESS",
                "callback_status": "RESULT_ACK",
            }
        ],
    )

    assert len(summaries) == 1
    assert summaries[0]["record_count"] == 3
    assert summaries[0]["record_types"] == ["acknowledgement", "event", "result"]
    assert summaries[0]["delivery_state"] == "COLLECTED"


def test_default_root_uses_environment(monkeypatch, tmp_path) -> None:
    root = tmp_path / "environment-worker"
    monkeypatch.setenv("XHS_MANAGER_WORKER_HOME", str(root))

    service = ManagerWorkerService(account_summary_reader=lambda: [])

    assert service.root == root.resolve()


def test_health_is_degraded_when_local_application_service_is_unavailable(tmp_path) -> None:
    service = ManagerWorkerService(
        tmp_path / "worker",
        account_summary_reader=lambda: [],
        local_service_probe=lambda: {"success": False},
    )
    service.configure(
        machine_id="PC001",
        role="executor",
        project_version=PROJECT_VERSION,
        package_root=str(tmp_path / "packages"),
        task_inbox_root=str(tmp_path / "inbox"),
    )

    health = service.health()["health"]

    assert health["status"] == "DEGRADED"
    assert health["healthy"] is False
    assert health["executor_service"]["ready"] is False
    assert health["local_service"]["ready"] is False


def test_executor_status_start_and_restart_return_fresh_health(tmp_path) -> None:
    local_ready = {"value": False}
    lifecycle_calls: list[str] = []

    def probe() -> dict:
        return {"success": local_ready["value"]}

    def start() -> dict:
        lifecycle_calls.append("start")
        local_ready["value"] = True
        return {"success": True, "status": "started"}

    def restart() -> dict:
        lifecycle_calls.append("restart")
        return {"success": True, "status": "restarted"}

    service = ManagerWorkerService(
        tmp_path / "worker",
        account_summary_reader=lambda: [],
        local_service_probe=probe,
        executor_starter=start,
        executor_restarter=restart,
    )
    service.configure(
        machine_id="PC001",
        role="executor",
        project_version=PROJECT_VERSION,
        package_root=str(tmp_path / "packages"),
        task_inbox_root=str(tmp_path / "inbox"),
    )

    before = dispatch_request(service, {"action": "executor-status"})
    started = dispatch_request(service, {"action": "start-executor"})
    restarted = dispatch_request(service, {"action": "restart-executor"})
    legacy = dispatch_request(service, {"action": "webui-status"})

    assert before["status"] == "STOPPED"
    assert before["executor_service"]["ready"] is False
    assert started["success"] is True
    assert started["health"]["executor_service"]["ready"] is True
    assert restarted["success"] is True
    assert restarted["operation"]["status"] == "restarted"
    assert legacy["webui"] == legacy["executor_service"]
    assert lifecycle_calls == ["start", "restart"]


def test_account_protocol_uses_api_only_service_and_returns_fresh_status(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    calls: list[tuple[str, str, dict | None, str]] = []

    def local_api(
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str = "",
        timeout_seconds: float | None = None,
    ) -> dict:
        calls.append((method, path, body, token))
        if path == "/session":
            return {"success": True, "session_token": "session-1"}
        if path == "/accounts":
            return {
                "success": True,
                "accounts": [
                    {
                        "name": "alpha",
                        "platform": "xiaohongshu",
                        "profile_mode": "existing",
                        "extension_instance_enrolled": True,
                    }
                ],
            }
        if path == "/accounts/alpha/status":
            return {
                "success": True,
                "status": "READY",
                "ready": True,
                "server_running": True,
                "extension_connected": True,
                "profile_verified": True,
                "connection_ready": True,
                "identity": {
                    "user_id": "uid-1",
                    "live_user_id": "uid-1",
                    "nickname": "Alpha",
                    "matches_record": True,
                },
                "account": {"name": "alpha", "platform": "xiaohongshu"},
            }
        if path == "/accounts/alpha/doctor":
            return {"success": True, "diagnosis": {"status": "READY"}}
        if method == "POST" and path == "/accounts/alpha/setup/begin":
            assert timeout_seconds == 60.0
            assert body == {"confirmed": True}
            assert token == "session-1"
            return {"success": True, "setup": {"phase": "CONNECTION_READY"}}
        raise AssertionError(f"unexpected local API request: {method} {path}")

    service._local_api_request = local_api  # type: ignore[method-assign]

    listed = dispatch_request(service, {"action": "list-accounts"})
    status = dispatch_request(
        service,
        {"action": "account-status", "payload": {"account": "alpha"}},
    )
    doctor = dispatch_request(
        service,
        {"action": "account-doctor", "payload": {"account": "alpha"}},
    )
    started = dispatch_request(
        service,
        {
            "action": "account-action",
            "payload": {"account": "alpha", "account_action": "profile-start"},
        },
    )

    assert listed["accounts"][0]["status"] == "READY"
    assert listed["accounts"][0]["identity"]["live_user_id"] == "uid-1"
    assert status["account"]["connection_ready"] is True
    assert doctor["diagnosis"]["diagnosis"]["status"] == "READY"
    assert started["success"] is True
    assert started["account"]["status"] == "READY"
    assert ("POST", "/accounts/alpha/setup/begin", {"confirmed": True}, "session-1") in calls

    history = service._store.snapshot()["profile_startups"]
    assert history[-1]["account"] == "alpha"
    assert history[-1]["phase"] == "CONNECTION_READY"
    assert started["operation"]["startup_measurement"] == history[-1]
    assert "启动请求耗时" in started["message"]


def test_identity_check_allows_page_navigation_timeout(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    observed_timeout: list[float | None] = []

    def local_api(
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str = "",
        timeout_seconds: float | None = None,
    ) -> dict:
        if path == "/session":
            return {"success": True, "session_token": "session-1"}
        if method == "POST" and path == "/accounts/alpha/identity/check":
            observed_timeout.append(timeout_seconds)
            return {"success": True, "identity": {"user_id": "uid-1"}}
        if path == "/accounts/alpha/status":
            return {
                "success": True,
                "status": "READY",
                "ready": True,
                "server_running": True,
                "extension_connected": True,
                "profile_verified": True,
                "connection_ready": True,
                "identity": {"live_user_id": "uid-1", "matches_record": True},
                "account": {"name": "alpha", "platform": "xiaohongshu"},
            }
        raise AssertionError(f"unexpected local API request: {method} {path}")

    service._local_api_request = local_api  # type: ignore[method-assign]

    result = dispatch_request(
        service,
        {
            "action": "account-action",
            "payload": {"account": "alpha", "account_action": "identity-check"},
        },
    )

    assert result["status"] == "READY"
    assert observed_timeout == [90.0]


def test_worker_forwards_identity_confirmation_with_expected_uid(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    calls = []

    def local_api(
        method: str,
        path: str,
        body: dict | None = None,
        *,
        token: str = "",
        timeout_seconds: float | None = None,
    ) -> dict:
        if path == "/session":
            return {"session_token": "test-session"}
        calls.append((method, path, body, token, timeout_seconds))
        return {"success": True, "identity": {"current": {"user_id": "red-user"}}}

    service._local_api_request = local_api
    values = {"confirmed": True, "expected_user_id": "red-user"}
    result = service.account_lifecycle(
        {
            "account": "red account",
            "action": "identity-record",
            "values": values,
        }
    )
    assert result["identity"]["current"]["user_id"] == "red-user"
    assert calls == [
        (
            "POST",
            "/accounts/red%20account/identity/record",
            values,
            "test-session",
            90,
        )
    ]


def test_account_field_request_is_visible_and_completed_after_identity_readback(
    tmp_path,
) -> None:
    service = make_service(tmp_path / "worker")

    def local_api(
        _method: str,
        path: str,
        _body: dict | None = None,
        *,
        token: str = "",
        timeout_seconds: float | None = None,
    ) -> dict:
        assert token == ""
        assert timeout_seconds is None
        if path == "/accounts/alpha/status":
            return {
                "success": True,
                "status": "READY",
                "ready": True,
                "server_running": True,
                "extension_connected": True,
                "profile_verified": True,
                "identity": {"live_user_id": "uid-1", "nickname": "Alpha"},
                "account": {"name": "alpha", "platform": "xiaohongshu"},
            }
        raise AssertionError(f"unexpected local API request: {path}")

    service._local_api_request = local_api  # type: ignore[method-assign]
    created = dispatch_request(
        service,
        {
            "action": "account-field-request",
            "payload": {
                "request_id": "ACCOUNT-001",
                "account": "alpha",
                "request_type": "LOGIN",
                "instructions": "请登录并核对 UID",
            },
        },
    )
    management = dispatch_request(
        service,
        {
            "action": "account-management",
            "payload": {
                "account": "alpha",
                "account_note": "经理机备注",
                "scheduling_enabled": False,
            },
        },
    )
    started = service.start_account_field_action("ACCOUNT-001")
    completed = service.complete_account_field_action("ACCOUNT-001")
    snapshot = service.dashboard_snapshot()

    assert created["field_request"]["state"] == "PENDING"
    assert started["field_request"]["state"] == "IN_PROGRESS"
    assert completed["field_request"]["state"] == "COMPLETED"
    refreshed = service.account_status("alpha")["account"]
    assert refreshed["field_request"]["state"] == "COMPLETED"
    assert refreshed["management"]["scheduling_enabled"] is False
    assert completed["field_request"]["identity"]["live_user_id"] == "uid-1"
    assert management["management"]["account_note"] == "经理机备注"
    assert snapshot["account_requests"][0]["request_id"] == "ACCOUNT-001"
    assert (
        snapshot["machine"]["account_slots"]["items"][0]["management"]["scheduling_enabled"]
        is False
    )
    assert snapshot["machine"]["account_slots"]["items"][0]["field_request"]["state"] == "COMPLETED"


def test_health_includes_detailed_api_only_account_status_for_manager_sync(tmp_path) -> None:
    service = ManagerWorkerService(
        tmp_path / "worker",
        local_service_probe=lambda: {"success": True},
    )
    service._account_summary_reader = lambda: [  # type: ignore[method-assign]
        {"name": "alpha", "platform": "xiaohongshu", "paired": True}
    ]

    def local_api(
        _method: str,
        path: str,
        _body: dict | None = None,
        *,
        token: str = "",
    ) -> dict:
        assert token == ""
        if path == "/accounts":
            return {"success": True, "accounts": [{"name": "alpha"}]}
        if path == "/accounts/alpha/status":
            return {
                "success": True,
                "status": "LOGIN_REQUIRED",
                "ready": False,
                "server_running": True,
                "extension_connected": True,
                "profile_verified": True,
                "identity": {"logged_out": True},
                "next_action": "请在绑定的 Profile 中登录账号",
                "account": {"name": "alpha", "platform": "xiaohongshu"},
            }
        raise AssertionError(path)

    service._local_api_request = local_api  # type: ignore[method-assign]
    service.configure(
        machine_id="PC001",
        role="executor",
        project_version=PROJECT_VERSION,
        package_root=str(tmp_path / "packages"),
        task_inbox_root=str(tmp_path / "inbox"),
    )

    health = service.health()["health"]

    assert health["status"] == "READY"
    assert health["account_slots"]["items"][0]["status"] == "LOGIN_REQUIRED"
    assert health["account_slots"]["items"][0]["identity"]["logged_out"] is True


def test_accept_task_is_idempotent_and_validates_route_and_version(tmp_path) -> None:
    service = make_service(tmp_path / "worker")

    first = service.accept_task(task_request())
    duplicate = service.accept_task(task_request())

    assert first["execution"]["state"] == "ACCEPTED"
    assert duplicate["execution"]["idempotent"] is True
    assert (
        duplicate["execution"]["remote_execution_id"] == first["execution"]["remote_execution_id"]
    )
    with pytest.raises(WorkerServiceError) as mismatch:
        service.accept_task({**task_request(task_id="task-2"), "target_machine_id": "PC999"})
    assert mismatch.value.code == "TARGET_MACHINE_MISMATCH"
    with pytest.raises(WorkerServiceError) as incompatible:
        service.accept_task(
            {
                **task_request(task_id="task-2", dispatch_id="dispatch-2"),
                "allowed_versions": ["2.0"],
            }
        )
    assert incompatible.value.code == "PROJECT_VERSION_NOT_ALLOWED"
    with pytest.raises(WorkerServiceError) as conflict:
        service.accept_task(task_request(task_id="task-2"))
    assert conflict.value.code == "TASK_IDEMPOTENCY_CONFLICT"


def test_wake_task_claims_sftp_file_starts_runner_and_persists_ack(tmp_path: Path) -> None:
    started: list[list[str]] = []

    class Process:
        pid = 4321

    def process_starter(command: list[str], **_kwargs: object) -> Process:
        started.append(command)
        return Process()

    worker_root = tmp_path / "worker"
    inbox_root = tmp_path / "exchange" / "inbox"
    outbox_root = tmp_path / "exchange" / "outbox"
    service = ManagerWorkerService(
        worker_root,
        account_summary_reader=lambda: [],
        local_control=lambda _action, _task_id: {"success": True},
        process_starter=process_starter,
    )
    service.configure(
        machine_id="PC001",
        role="executor",
        project_version=PROJECT_VERSION,
        package_root=str(tmp_path / "packages"),
        task_inbox_root=str(inbox_root),
        task_outbox_root=str(outbox_root),
    )
    task_path = inbox_root / "pending" / "task-1.json"
    task_path.write_text(json.dumps(task_request(), ensure_ascii=False), encoding="utf-8")

    acknowledgement = service.wake_task("task-1")
    repeated = service.wake_task("task-1")
    index = json.loads((outbox_root / "index.json").read_text(encoding="utf-8"))

    assert acknowledgement["status"] == "TASK_ACK"
    assert acknowledgement["acknowledgement"]["status"] == "ACCEPTED"
    assert acknowledgement["acknowledgement"]["runner_pid"] == 4321
    assert repeated["status"] == "TASK_ACK"
    assert len(started) == 1
    assert not task_path.exists()
    assert (inbox_root / "claimed" / "task-1.json").is_file()
    assert [item["record_type"] for item in index["records"]] == ["acknowledgement"]


def test_progress_and_result_are_written_to_sftp_outbox(tmp_path: Path) -> None:
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "message": "任务完成",
            "item_results": [{"target_id": "note-1", "state": "SUCCESS"}],
        },
    )
    accepted = service.accept_task(task_request())
    execution_id = accepted["execution"]["remote_execution_id"]

    progress = service.send_task_progress("task-1", remote_execution_id=execution_id)
    service.run_task("task-1", remote_execution_id=execution_id)
    result = service.push_task_result("task-1", remote_execution_id=execution_id)
    config = service._store.snapshot()["config"]
    index = json.loads(Path(config["task_outbox_root"]).joinpath("index.json").read_text())

    assert progress["status"] == "OUTBOX_READY"
    assert result["status"] == "RESULT_READY"
    assert [item["record_type"] for item in index["records"]] == [
        "event",
        "result",
        "event",
    ]
    assert index["records"][-1]["record_id"].endswith("progress:outbox_written")


def test_completed_result_uses_outbox_for_recovery_without_growing_worker_state(
    tmp_path: Path,
) -> None:
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "business_result": {"notes": ["note-1"] * 300},
        },
    )
    execution_id = service.accept_task(task_request())["execution"]["remote_execution_id"]
    service.run_task("task-1", remote_execution_id=execution_id)
    original = service.collect_results(0)["results"][0]

    service.push_task_result("task-1", remote_execution_id=execution_id)
    stored = service._store.snapshot()["results"][0]
    assert "business_result" not in stored
    assert stored["outbox_relative_path"].startswith("results/")
    assert (
        service.collect_results(0)["results"][0]["business_result"] == original["business_result"]
    )
    assert (
        service.push_task_result("task-1", remote_execution_id=execution_id)["status"]
        == "RESULT_READY"
    )

    def restore_legacy_result(state: dict) -> None:
        state["results"][0] = service._store.load_result(state, state["results"][0])

    service._store.mutate(restore_legacy_result)
    assert service._store.compact_results() == 1
    assert service._store.compact_results() == 0
    assert (
        service.collect_results(0)["results"][0]["business_result"] == original["business_result"]
    )


def test_result_outbox_restores_account_identity_from_execution(tmp_path: Path) -> None:
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {"state": "SUCCESS", "business_result": {"notes": [1]}},
    )
    request = task_request()
    request["task"].update(platform="xiaohongshu", capability="collect-operations-metrics")
    execution_id = service.accept_task(request)["execution"]["remote_execution_id"]
    service.run_task("task-1", remote_execution_id=execution_id)

    def remove_identity(state: dict) -> None:
        for field in ("account_slot", "platform", "capability"):
            state["results"][-1].pop(field, None)

    service._store.mutate(remove_identity)
    service.push_task_result("task-1", remote_execution_id=execution_id)

    saved = service._store.snapshot()["results"][-1]
    index = json.loads((service.root / "task-outbox" / "index.json").read_text())
    record = next(row for row in index["records"] if row["record_type"] == "result")
    outbox = json.loads(
        (service.root / "task-outbox" / record["relative_path"]).read_text(encoding="utf-8")
    )
    for result in (saved, outbox["payload"]["result"]):
        assert result["account_slot"] == "alpha"
        assert result["platform"] == "xiaohongshu"
        assert result["capability"] == "collect-operations-metrics"


def test_worker_records_capability_progress_before_final_result(tmp_path: Path) -> None:
    request = task_request()
    request["task"] = {
        **request["task"],
        "capability": "home-engagement",
        "parameters": {"browse_count": 2, "like_count": 1},
    }
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "message": "首页浏览点赞完成：浏览 2 篇，点赞 1 篇",
            "requested": {"browse_count": 2, "like_count": 1},
            "counts": {"browsed": 2, "liked": 1},
            "items": [
                {"feed_id": "feed-1", "like": {"success": True}},
                {"feed_id": "feed-2", "like": None},
            ],
        },
    )
    accepted = service.accept_task(request)
    execution_id = accepted["execution"]["remote_execution_id"]

    service.run_task("task-1", remote_execution_id=execution_id)
    progress = service.record_outcome_progress("task-1", remote_execution_id=execution_id)
    service.push_task_result("task-1", remote_execution_id=execution_id)

    browsing = next(
        item for item in progress["agent_messages"] if item["payload"].get("stage") == "browsing"
    )
    assert browsing["payload"]["metrics"] == {
        "browse": {"actual": 2, "target": 2},
        "like": {"actual": 1, "target": 1},
    }


def test_worker_records_context_comment_and_submission_stages(tmp_path: Path) -> None:
    request = task_request()
    request["task"] = {
        **request["task"],
        "capability": "post-comment",
        "parameters": {"count": 1},
    }
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "message": "随机评论完成，共发送 1 条评论",
            "items": [
                {
                    "feed_id": "feed-1",
                    "title": "露营收纳清单",
                    "author": "小满",
                    "content": "分区装袋这个细节很实用。",
                    "status": "success",
                    "success": True,
                }
            ],
        },
    )
    accepted = service.accept_task(request)
    execution_id = accepted["execution"]["remote_execution_id"]

    service.run_task("task-1", remote_execution_id=execution_id)
    progress = service.record_outcome_progress("task-1", remote_execution_id=execution_id)

    assert [item["payload"]["stage"] for item in progress["agent_messages"]] == [
        "reading_context",
        "comment_generated",
        "platform_submitted",
    ]
    assert progress["agent_messages"][1]["payload"]["content"] == "分区装袋这个细节很实用。"
    assert progress["agent_messages"][2]["payload"]["metrics"]["comment"] == {
        "actual": 1,
        "target": 1,
    }


def test_configure_rejects_project_version_change_until_tasks_are_final(tmp_path) -> None:
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {"state": "SUCCESS"},
    )
    service.accept_task(task_request())

    with pytest.raises(WorkerServiceError) as active:
        service.configure(project_version="1.6.0")

    assert active.value.code == "CONFIG_IN_USE"
    service.run_task(task_id="task-1")
    configured = service.configure(project_version="1.6.0")
    assert configured["config"]["project_version"] == "1.6.0"


def test_configure_allows_outbox_migration_when_only_transfer_history_exists(tmp_path) -> None:
    service = make_service(tmp_path / "worker")

    def add_transfer_history(state: dict) -> None:
        state["transfers"]["transfer-1"] = {"state": "SUCCESS"}

    service._store.mutate(add_transfer_history)
    outbox_root = tmp_path / "manager-task-outbox"

    configured = service.configure(task_outbox_root=str(outbox_root))

    assert configured["config"]["task_outbox_root"] == str(outbox_root.resolve())
    assert outbox_root.joinpath("index.json").exists()


def test_run_task_persists_items_evidence_incremental_data_and_sequence(tmp_path) -> None:
    calls = []

    def executor(payload: dict) -> dict:
        calls.append(payload)
        return {
            "state": "SUCCESS",
            "message": "已完成",
            "item_results": [
                {"item_id": "feed-1", "state": "SUCCESS"},
                {"item_id": "feed-2", "state": "FAILED"},
            ],
            "evidence_index": {"feed-1": ["record-1"]},
            "incremental_data": [{"kind": "feed", "id": "feed-1"}],
        }

    service = make_service(tmp_path / "worker", executor=executor)
    accepted = service.accept_task(task_request())["execution"]

    completed = service.run_task(remote_execution_id=accepted["remote_execution_id"])
    repeated_run = service.run_task(task_id="task-1")
    first_collection = service.collect_results(0)
    repeated_collection = service.collect_results(0)

    assert completed["execution"]["state"] == "SUCCESS"
    assert completed["execution"]["item_results"][1]["state"] == "FAILED"
    assert completed["execution"]["evidence_index"] == {"feed-1": ["record-1"]}
    assert completed["execution"]["incremental_data"][0]["id"] == "feed-1"
    assert completed["execution"]["manual_action_required"] is False
    assert repeated_run["execution"]["idempotent"] is True
    assert len(calls) == 1
    assert first_collection == repeated_collection
    assert first_collection["results"][0]["sequence"] == 1
    assert first_collection["results"][0]["remote_execution_id"] == accepted["remote_execution_id"]
    assert first_collection["results"][0]["actual_version"] == PROJECT_VERSION
    assert service.collect_results(first_collection["next_sequence"])["results"] == []


def test_worker_retains_authorization_for_audited_execution(tmp_path) -> None:
    calls: list[dict] = []
    service = make_service(
        tmp_path / "worker",
        executor=lambda payload: calls.append(payload) or {"state": "SUCCESS"},
    )
    request = {
        **task_request(),
        "task": {
            **task_request()["task"],
            "authorization": {"approval_id": "approval-1", "scope": "search"},
            "write_intent": False,
        },
    }

    accepted = service.accept_task(request)["execution"]
    service.run_task(task_id="task-1")

    assert accepted["task"]["authorization"]["approval_id"] == "approval-1"
    assert calls[0]["authorization"]["scope"] == "search"
    assert calls[0]["write_intent"] is False


def test_worker_forwards_manager_context_to_local_task_contract(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    created_bodies: list[dict] = []

    execution_timeouts: list[float | None] = []

    def local_request(
        method: str,
        path: str,
        body=None,
        *,
        token: str = "",
        timeout_seconds: float | None = None,
    ) -> dict:
        if path == "/session":
            return {"session_token": "session-1"}
        assert token == "session-1"
        if method == "POST" and path == "/tasks":
            created_bodies.append(body)
            return {"task": {"task_id": "local-task-1", "state": "QUEUED"}}
        if method == "POST" and path == "/tasks/local-task-1/execute":
            execution_timeouts.append(timeout_seconds)
            return {"state": "SUCCESS", "message": "完成"}
        raise AssertionError(f"unexpected local request: {method} {path}")

    service._local_api_request = local_request  # type: ignore[method-assign]
    payload = {
        **task_request()["task"],
        "remote_execution_id": "execution-1",
        "schedule_id": "schedule-1",
        "operation_task_id": "operation-1",
        "goal_id": "goal-1",
        "goal_snapshot": {"goal_id": "goal-1", "version": 3},
        "persona_snapshot": {"persona_id": "persona-1", "version": 2},
        "timeout_seconds": 600,
    }

    result = service._execute_via_local_api(payload)

    assert result["state"] == "SUCCESS"
    assert created_bodies[0]["schedule_id"] == "schedule-1"
    assert created_bodies[0]["operation_task_id"] == "operation-1"
    assert created_bodies[0]["goal_id"] == "goal-1"
    assert created_bodies[0]["goal_snapshot"] == {"goal_id": "goal-1", "version": 3}
    assert created_bodies[0]["persona_snapshot"] == {
        "persona_id": "persona-1",
        "version": 2,
    }
    assert execution_timeouts == [630.0]


def test_result_data_status_distinguishes_missing_from_observed_empty(tmp_path) -> None:
    missing_service = make_service(
        tmp_path / "missing",
        executor=lambda _payload: {"state": "SUCCESS", "message": "完成"},
    )
    missing_service.accept_task(task_request())
    missing_service.run_task(task_id="task-1")
    missing = missing_service.collect_results(0)["results"][0]

    observed_service = make_service(
        tmp_path / "observed",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "message": "完成且确认无逐项与增量记录",
            "business_result": [],
            "item_results": [],
            "incremental_data": [],
        },
    )
    observed_service.accept_task(task_request())
    observed_service.run_task(task_id="task-1")
    observed = observed_service.collect_results(0)["results"][0]

    assert missing["item_results_status"] == "missing"
    assert missing["item_results_missing_reason"]
    assert missing["incremental_data_status"] == "missing"
    assert missing["incremental_data_missing_reason"]
    assert observed["business_result_status"] == "observed"
    assert observed["business_result"] == []
    assert observed["item_results_status"] == "observed"
    assert observed["item_results"] == []
    assert observed["incremental_data_status"] == "observed"
    assert observed["incremental_data"] == []


def test_search_business_result_is_published_for_manager_collection(tmp_path) -> None:
    feeds = [
        {"id": "feed-1", "displayTitle": "露营地点"},
        {"id": "feed-2", "displayTitle": "露营装备"},
    ]
    service = make_service(
        tmp_path / "search-result",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "result": {"feeds": feeds, "count": len(feeds)},
        },
    )
    service.accept_task(task_request())

    completed = service.run_task(task_id="task-1")["execution"]
    collected = service.collect_results(0)["results"][0]

    assert completed["business_result_status"] == "observed"
    assert completed["business_result"] == {"feeds": feeds, "count": 2}
    assert completed["item_results_status"] == "observed"
    assert completed["item_results"] == feeds
    assert collected["business_result"] == {"feeds": feeds, "count": 2}
    assert collected["item_results"] == feeds


def test_explicit_failed_business_result_status_is_preserved(tmp_path) -> None:
    service = make_service(
        tmp_path / "failed-business-result",
        executor=lambda _payload: {
            "state": "FAILED",
            "business_result": {},
            "business_result_status": "failed",
            "business_result_missing_reason": "页面没有返回业务数据",
        },
    )
    service.accept_task(task_request())

    completed = service.run_task(task_id="task-1")["execution"]
    collected = service.collect_results(0)["results"][0]

    assert completed["business_result_status"] == "failed"
    assert completed["business_result_missing_reason"] == "页面没有返回业务数据"
    assert collected["business_result_status"] == "failed"
    assert collected["business_result"] == {}


def test_executor_crash_keeps_agent_progress_for_reconciliation(tmp_path) -> None:
    def interrupted(payload: dict) -> dict:
        run = tmp_path / "worker" / "agent-runs" / payload["remote_execution_id"]
        run.mkdir(parents=True)
        (run / "step-progress.json").write_text('{"completed_steps": 1}', encoding="utf-8")
        (run / "engagement.bridge.progress.json").write_text('{"browsed": 2}', encoding="utf-8")
        raise ConnectionError("员工 Agent 连接中断")

    service = make_service(tmp_path / "worker", executor=interrupted)
    request = task_request()
    request["task"]["write_intent"] = True
    service.accept_task(request)
    execution = service.run_task(task_id="task-1")["execution"]

    assert execution["state"] == "RESULT_UNKNOWN"
    assert execution["business_result"]["step_checkpoint"]["completed_steps"] == 1
    assert len(execution["evidence_index"]["bridge_progress"]) == 1
    assert "ConnectionError" in execution["message"]


@pytest.mark.parametrize(
    "state",
    ["PARTIAL_SUCCESS", "CANCELLED", "FAILED", "BLOCKED", "RESULT_UNKNOWN"],
)
def test_run_task_preserves_supported_terminal_states(tmp_path, state) -> None:
    service = make_service(
        tmp_path / state.lower(),
        executor=lambda _payload: {
            "state": state,
            "error_code": f"{state}_CODE",
            "message": "需要处理",
        },
    )
    service.accept_task(task_request())

    result = service.run_task(task_id="task-1")["execution"]

    assert result["state"] == state
    assert result["error_code"] == f"{state}_CODE"
    assert result["manual_action_required"] is (state in {"FAILED", "BLOCKED", "RESULT_UNKNOWN"})
    assert result["error_classification"]


def test_unstarted_task_pause_and_resume_are_idempotent(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    service.accept_task(task_request())

    paused = service.pause_task(task_id="task-1")
    paused_again = service.pause_task(task_id="task-1")
    assert service.collect_results(0)["results"] == []
    resumed = service.resume_task(task_id="task-1")
    resumed_again = service.resume_task(task_id="task-1")

    assert paused["execution"]["state"] == "PAUSED"
    assert paused_again["execution"]["idempotent"] is True
    assert resumed["execution"]["state"] == "ACCEPTED"
    assert resumed_again["execution"]["idempotent"] is True


def test_running_pause_requests_local_cancel_and_requires_reconciliation(tmp_path) -> None:
    started = threading.Event()
    release = threading.Event()
    local_calls: list[tuple[str, str | None]] = []
    service: ManagerWorkerService

    def executor(payload: dict) -> dict:
        service._record_local_task_id(payload["remote_execution_id"], "local-task-1")
        started.set()
        assert release.wait(timeout=5)
        return {"state": "SUCCESS", "message": "页面动作返回成功"}

    service = make_service(
        tmp_path / "worker",
        executor=executor,
        local_calls=local_calls,
    )
    service.accept_task(task_request())
    completed: list[dict] = []
    thread = threading.Thread(target=lambda: completed.append(service.run_task(task_id="task-1")))
    thread.start()
    assert started.wait(timeout=5)

    pause = service.pause_task(task_id="task-1")
    release.set()
    thread.join(timeout=5)

    assert pause["execution"]["state"] == "PAUSE_REQUESTED"
    assert pause["execution"]["local_cancel"]["success"] is True
    assert ("cancel-task", "local-task-1") in local_calls
    assert completed[0]["execution"]["state"] == "RECONCILIATION_REQUIRED"
    assert completed[0]["execution"]["observed_executor_outcome"]["state"] == "SUCCESS"
    assert completed[0]["execution"]["manual_action_required"] is True
    assert service.collect_results(0)["results"] == []
    assert service.task_status(task_id="task-1")["execution"]["state"] == "SUCCESS"
    assert service.collect_results(0)["results"][0]["state"] == "SUCCESS"


@pytest.mark.parametrize("local_state", ["PARTIAL_SUCCESS", "CANCELLED"])
def test_status_reconciles_interrupted_run_from_local_terminal_state(
    monkeypatch,
    tmp_path,
    local_state,
) -> None:
    service = make_service(tmp_path / local_state.lower())
    accepted = service.accept_task(task_request())["execution"]

    def mark_interrupted(state: dict) -> None:
        execution = state["executions"][accepted["remote_execution_id"]]
        execution["state"] = "RUNNING"
        execution["local_task_id"] = "local-task-1"

    service._store.mutate(mark_interrupted)
    calls: list[tuple[str, str]] = []

    def local_request(method: str, path: str, _body=None, *, token: str = "") -> dict:
        calls.append((method, path))
        if path == "/session":
            return {"session_token": "session-1"}
        assert token == "session-1"
        if path == "/operation-events":
            return {"success": True, "events": []}
        return {
            "success": True,
            "task": {
                "task_id": "local-task-1",
                "state": local_state,
                "result_summary": "本地任务已有终态",
            },
        }

    monkeypatch.setattr(service, "_local_api_request", local_request)

    status = service.task_status(task_id="task-1")

    assert status["execution"]["state"] == local_state
    assert calls == [
        ("GET", "/session"),
        ("GET", "/tasks/local-task-1"),
        ("GET", "/operation-events"),
    ]
    assert service.collect_results(0)["results"][0]["state"] == local_state


def test_status_revises_unknown_result_from_local_operation_event(monkeypatch, tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    accepted = service.accept_task(task_request())["execution"]

    def mark_unknown(state: dict) -> None:
        execution = state["executions"][accepted["remote_execution_id"]]
        execution["state"] = "RESULT_UNKNOWN"
        execution["local_task_id"] = "local-task-1"
        execution["message"] = "本地执行回读中断"
        service._append_result(state, execution)

    service._store.mutate(mark_unknown)
    receipt = service._store.snapshot()["results"][-1]
    assert receipt["account_slot"] == "alpha"
    assert receipt["capability"] == "search-feeds"

    def local_request(method: str, path: str, _body=None, *, token: str = "") -> dict:
        if path == "/session":
            return {"session_token": "session-1"}
        assert token == "session-1"
        if path == "/tasks/local-task-1":
            return {
                "success": True,
                "task": {
                    "task_id": "local-task-1",
                    "state": "SUCCESS",
                    "result_summary": "首页浏览点赞完成：浏览 10 篇，点赞 3 篇",
                },
            }
        if path == "/operation-events":
            return {
                "success": True,
                "events": [
                    {
                        "task_id": "local-task-1",
                        "result_summary": "首页浏览点赞完成：浏览 10 篇，点赞 3 篇",
                        "platform_result": {
                            "items": [
                                {
                                    "feed_id": "feed-1",
                                    "browse": {"status": "success"},
                                    "like": {"status": "success", "success": True},
                                }
                            ]
                        },
                    }
                ],
            }
        raise AssertionError(f"unexpected local request: {method} {path}")

    monkeypatch.setattr(service, "_local_api_request", local_request)

    status = service.task_status(task_id="task-1")["execution"]
    results = service.collect_results(0)["results"]

    assert status["state"] == "SUCCESS"
    assert status["item_results"][0]["feed_id"] == "feed-1"
    assert [item["state"] for item in results] == ["RESULT_UNKNOWN", "SUCCESS"]
    assert results[1]["result_revision"] == 2
    outbox_index = json.loads(
        Path(service._store.snapshot()["config"]["task_outbox_root"])
        .joinpath("index.json")
        .read_text(encoding="utf-8")
    )
    assert [item["record_type"] for item in outbox_index["records"]] == ["result"]
    assert outbox_index["records"][0]["record_id"] == "dispatch-1:result:2"


def test_status_marks_interrupted_run_unknown_when_local_readback_fails(
    monkeypatch,
    tmp_path,
) -> None:
    service = make_service(tmp_path / "worker")
    accepted = service.accept_task(task_request())["execution"]

    def mark_interrupted(state: dict) -> None:
        execution = state["executions"][accepted["remote_execution_id"]]
        execution["state"] = "RUNNING"
        execution["local_task_id"] = "local-task-1"

    service._store.mutate(mark_interrupted)

    def local_request(method: str, path: str, _body=None, *, token: str = "") -> dict:
        if path == "/session":
            return {"session_token": "session-1"}
        raise WorkerServiceError("LOCAL_SERVICE_UNAVAILABLE", "本地应用服务不可用")

    monkeypatch.setattr(service, "_local_api_request", local_request)

    status = service.task_status(task_id="task-1")["execution"]

    assert status["state"] == "RESULT_UNKNOWN"
    assert status["reconciliation_required"] is True
    assert status["error_code"] == "LOCAL_SERVICE_UNAVAILABLE"


def test_machine_pause_and_resume_sync_local_api_and_restore_unstarted_tasks(tmp_path) -> None:
    local_calls: list[tuple[str, str | None]] = []
    service = make_service(tmp_path / "worker", local_calls=local_calls)
    service.accept_task(task_request())

    paused = service.pause_machine()
    paused_again = service.pause_machine()
    resumed = service.resume_machine()
    resumed_again = service.resume_machine()

    assert paused["success"] is True
    assert paused["machine"]["paused"] is True
    assert service.task_status(task_id="task-1")["execution"]["state"] == "ACCEPTED"
    assert paused_again["machine"]["idempotent"] is True
    assert resumed["machine"]["paused"] is False
    assert resumed_again["machine"]["idempotent"] is True
    assert local_calls == [
        ("pause-machine", None),
        ("pause-machine", None),
        ("resume-machine", None),
        ("resume-machine", None),
    ]


@pytest.mark.parametrize("package_type", ["task_package", "function_package", "config"])
def test_transfer_installs_each_supported_type_once(tmp_path, package_type) -> None:
    service = make_service(tmp_path / package_type)
    name = f"{package_type}-sample"
    prepared = service.prepare_transfer(
        transfer_id="transfer-1",
        package_type=package_type,
        package_name=name,
        version="1.0",
        target_machine_id="PC001",
        allowed_versions=[PROJECT_VERSION],
    )
    staging = Path(prepared["transfer"]["staging_path"])
    (staging / "manifest.json").write_text(
        json.dumps(manifest(package_type, name, "1.0")),
        encoding="utf-8",
    )
    (staging / "payload.txt").write_text("version-one", encoding="utf-8")
    if package_type == "function_package":
        write_function_package_files(staging)

    finalized = service.finalize_transfer("transfer-1")
    repeated = service.finalize_transfer("transfer-1")

    installed = Path(finalized["transfer"]["installed_path"])
    assert finalized["success"] is True
    assert finalized["transfer"]["state"] == "SUCCESS"
    assert finalized["transfer"]["actual_version"] == "1.0"
    assert installed.joinpath("payload.txt").read_text(encoding="utf-8") == "version-one"
    assert repeated["transfer"]["idempotent"] is True


def test_transfer_never_overwrites_version_and_failure_preserves_old(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    package_type = "function_package"
    package_name = "EXPLORE-01"
    first = service.prepare_transfer(
        transfer_id="transfer-1",
        package_type=package_type,
        package_name=package_name,
        version="1.0",
    )
    first_staging = Path(first["transfer"]["staging_path"])
    (first_staging / "manifest.json").write_text(
        json.dumps(manifest(package_type, package_name, "1.0")),
        encoding="utf-8",
    )
    (first_staging / "payload.txt").write_text("stable", encoding="utf-8")
    write_function_package_files(first_staging, marker="stable")
    installed = Path(service.finalize_transfer("transfer-1")["transfer"]["installed_path"])

    collision = service.prepare_transfer(
        transfer_id="transfer-2",
        package_type=package_type,
        package_name=package_name,
        version="1.0",
    )
    collision_staging = Path(collision["transfer"]["staging_path"])
    (collision_staging / "manifest.json").write_text(
        json.dumps(manifest(package_type, package_name, "1.0")),
        encoding="utf-8",
    )
    (collision_staging / "payload.txt").write_text("replacement", encoding="utf-8")
    write_function_package_files(collision_staging, marker="replacement")
    collision_result = service.finalize_transfer("transfer-2")

    incompatible = service.prepare_transfer(
        transfer_id="transfer-3",
        package_type=package_type,
        package_name=package_name,
        version="2.0",
    )
    incompatible_staging = Path(incompatible["transfer"]["staging_path"])
    (incompatible_staging / "manifest.json").write_text(
        json.dumps(
            manifest(
                package_type,
                package_name,
                "2.0",
                allowed_versions=["9.9"],
            )
        ),
        encoding="utf-8",
    )
    write_function_package_files(incompatible_staging, marker="incompatible")
    incompatible_result = service.finalize_transfer("transfer-3")

    assert collision_result["success"] is False
    assert collision_result["transfer"]["error_code"] == "PACKAGE_VERSION_EXISTS"
    assert incompatible_result["success"] is False
    assert incompatible_result["transfer"]["error_code"] == ("MANIFEST_PROJECT_VERSION_MISMATCH")
    assert installed.joinpath("payload.txt").read_text(encoding="utf-8") == "stable"
    assert collision_staging.is_dir()
    assert incompatible_staging.is_dir()


def test_function_version_policy_selects_trial_then_stable_and_pins_result(tmp_path) -> None:
    service = make_service(
        tmp_path / "worker",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "item_results": [],
            "incremental_data": [],
        },
    )
    installed_1_0 = install_function_version(
        service,
        transfer_id="install-1-0",
        function_id="EXPLORE-01",
        version="1.0",
    )
    assert installed_1_0["transfer"]["version_status"] == "DISABLED"
    assert installed_1_0["transfer"]["stable_version"] == ""
    scope = {
        "manager_ids": ["MILA-MANAGER"],
        "machine_ids": ["PC001"],
        "account_slots": ["alpha", "beta"],
        "allowed_actions": ["search-feeds"],
    }
    stable = service.set_function_version_policy(
        function_id="EXPLORE-01",
        version="1.0",
        status="FORMAL",
        scope=scope,
        set_as_stable=True,
    )
    repeated = service.set_function_version_policy(
        function_id="EXPLORE-01",
        version="1.0",
        status="FORMAL",
        scope=scope,
        set_as_stable=True,
    )
    assert stable["version"]["stable"] is True
    assert repeated["idempotent"] is True

    install_function_version(
        service,
        transfer_id="install-1-1",
        function_id="EXPLORE-01",
        version="1.1",
    )
    service.set_function_version_policy(
        function_id="EXPLORE-01",
        version="1.1",
        status="TRIAL",
        scope={**scope, "account_slots": ["alpha"]},
    )

    alpha = task_request(task_id="alpha-task", dispatch_id="alpha-dispatch")
    alpha["task"].update(
        {
            "manager_id": "MILA-MANAGER",
            "function_id": "EXPLORE-01",
            "allowed_function_versions": ["*"],
        }
    )
    accepted_alpha = service.accept_task(alpha)["execution"]
    assert accepted_alpha["actual_version"] == "1.1"
    assert accepted_alpha["version_status"] == "TRIAL"

    with pytest.raises(WorkerServiceError) as in_use:
        service.set_function_version_policy(
            function_id="EXPLORE-01",
            version="1.1",
            status="DISABLED",
        )
    assert in_use.value.code == "FUNCTION_VERSION_IN_USE"
    completed_alpha = service.run_task(task_id="alpha-task")["execution"]
    collected_alpha = service.collect_results(0)["results"][0]
    assert completed_alpha["actual_version"] == "1.1"
    assert collected_alpha["actual_version"] == "1.1"
    assert collected_alpha["project_version"] == PROJECT_VERSION

    service.set_function_version_policy(
        function_id="EXPLORE-01",
        version="1.1",
        status="DISABLED",
    )
    beta = task_request(task_id="beta-task", dispatch_id="beta-dispatch")
    beta["task"].update(
        {
            "account_slot": "beta",
            "manager_id": "MILA-MANAGER",
            "function_id": "EXPLORE-01",
            "allowed_function_versions": ["*"],
        }
    )
    accepted_beta = service.accept_task(beta)["execution"]
    assert accepted_beta["actual_version"] == "1.0"
    assert accepted_beta["version_status"] == "FORMAL"
    versions = service.list_function_versions(function_id="EXPLORE-01")
    assert [item["status"] for item in versions["versions"]] == ["FORMAL", "DISABLED"]


def test_function_version_scope_rejects_an_unapproved_account(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    install_function_version(
        service,
        transfer_id="install-1-0",
        function_id="EXPLORE-01",
        version="1.0",
    )
    service.set_function_version_policy(
        function_id="EXPLORE-01",
        version="1.0",
        status="FORMAL",
        scope={
            "manager_ids": ["MILA-MANAGER"],
            "machine_ids": ["PC001"],
            "account_slots": ["alpha"],
            "allowed_actions": ["search-feeds"],
        },
        set_as_stable=True,
    )
    request = task_request()
    request["task"].update(
        {
            "account_slot": "beta",
            "manager_id": "MILA-MANAGER",
            "function_id": "EXPLORE-01",
            "allowed_function_versions": ["1.0"],
        }
    )

    with pytest.raises(WorkerServiceError) as rejected:
        service.accept_task(request)

    assert rejected.value.code == "FUNCTION_VERSION_NOT_ALLOWED"


def test_cross_drive_style_install_copies_to_target_before_activation(
    monkeypatch,
    tmp_path,
) -> None:
    service = make_service(tmp_path / "worker")
    prepared = service.prepare_transfer(
        transfer_id="cross-drive",
        package_type="function_package",
        package_name="EXPLORE-01",
        version="1.0",
    )
    staging = Path(prepared["transfer"]["staging_path"])
    staging.joinpath("manifest.json").write_text(
        json.dumps(manifest("function_package", "EXPLORE-01", "1.0")),
        encoding="utf-8",
    )
    write_function_package_files(staging)
    original_replace = Path.replace
    failed_once = False

    def cross_drive_replace(path: Path, target: Path | str) -> Path:
        nonlocal failed_once
        if path == staging and not failed_once:
            failed_once = True
            raise OSError("模拟跨磁盘移动")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", cross_drive_replace)

    result = service.finalize_transfer("cross-drive")

    installed = Path(result["transfer"]["installed_path"])
    assert result["success"] is True
    assert failed_once is True
    assert installed.joinpath("runtime", "run.py").is_file()
    assert not staging.exists()


def test_formal_function_manifest_uses_function_id_and_keeps_chinese_display_name(
    tmp_path,
) -> None:
    service = make_service(tmp_path / "worker")
    source_name = "账号功能包 1.0"
    prepared = service.prepare_transfer(
        {
            "transfer_id": "formal-transfer",
            "transfer_type": "function_package",
            "source_name": source_name,
        }
    )
    uploaded = Path(prepared["transfer"]["staging_path"]) / source_name
    uploaded.mkdir()
    uploaded.joinpath("manifest.json").write_text(
        json.dumps(
            {
                **manifest("function_package", "ACCOUNT-01", "1.0"),
                "business_module": "小红书自动化项目/账号模块",
                "function_name": "账号 Profile 发现与绑定",
                "package_name": "ACCOUNT-01 Profile发现、绑定和扩展配对",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    write_function_package_files(uploaded, marker="formal")

    result = service.finalize_transfer(
        {
            "transfer_id": "formal-transfer",
            "transfer_type": "function_package",
            "uploaded_path": str(uploaded),
            "allowed_versions": [PROJECT_VERSION],
        }
    )

    installed = Path(result["transfer"]["installed_path"])
    assert result["success"] is True
    assert result["transfer"]["display_name"] == "ACCOUNT-01 Profile发现、绑定和扩展配对"
    assert installed.parts[-2:] == ("ACCOUNT-01", "1.0")


@pytest.mark.parametrize("transfer_type", ["task_package", "config"])
def test_manager_protocol_accepts_file_transfer_without_manifest(tmp_path, transfer_type) -> None:
    service = make_service(tmp_path / transfer_type)
    source_name = "task.json" if transfer_type == "task_package" else "settings.json"
    prepared = service.prepare_transfer(
        {
            "transfer_id": f"{transfer_type}-transfer",
            "transfer_type": transfer_type,
            "source_name": source_name,
        }
    )
    uploaded = Path(prepared["transfer"]["staging_path"]) / source_name
    uploaded.write_text("{}", encoding="utf-8")

    finalized = service.finalize_transfer(
        {
            "transfer_id": f"{transfer_type}-transfer",
            "transfer_type": transfer_type,
            "uploaded_path": str(uploaded),
            "allowed_versions": [],
        }
    )

    installed = Path(finalized["transfer"]["installed_path"])
    assert finalized["success"] is True
    assert finalized["transfer"]["state"] == "SUCCESS"
    assert finalized["transfer"]["actual_version"] == f"{transfer_type}-transfer"
    assert installed.joinpath(source_name).read_text(encoding="utf-8") == "{}"


class DirectWorkerTransport:
    def __init__(self, worker: ManagerWorkerService) -> None:
        self.worker = worker
        self.calls: list[tuple[str, dict]] = []

    def invoke(
        self,
        _machine: dict,
        action: str,
        payload: dict,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        del timeout_seconds
        self.calls.append((action, dict(payload)))
        if action == "start-task":
            response = self.worker.run_task(
                task_id=payload.get("task_id"),
                remote_execution_id=payload.get("remote_execution_id"),
            )
            response["status"] = "TASK_ACK"
            return response
        return dispatch_request(self.worker, {"action": action, "payload": payload})

    def upload(
        self,
        _machine: dict,
        source: str,
        remote_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        del timeout_seconds
        source_path = Path(source)
        destination = Path(remote_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source_path.is_dir():
            shutil.copytree(source_path, destination)
        else:
            shutil.copy2(source_path, destination)
        return {"success": True}

    def download(
        self,
        _machine: dict,
        remote_path: str,
        destination: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        del timeout_seconds
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_path, destination)
        return {"success": True}

    def sftp_upload_atomic(
        self,
        _machine: dict,
        source: str,
        remote_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        del timeout_seconds
        destination = Path(remote_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        return {"success": True, "remote_path": remote_path}

    def sftp_download(
        self,
        _machine: dict,
        remote_path: str,
        destination: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        del timeout_seconds
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(remote_path, destination_path)
        return {"success": True, "remote_path": remote_path}


@pytest.mark.parametrize("batch", [False, True])
def test_manager_service_and_worker_share_health_transfer_task_and_cursor_protocol(
    tmp_path,
    batch,
) -> None:
    worker = make_service(
        tmp_path / "worker",
        project_version="0.1.0",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "message": "页面回读成功",
            "item_results": [{"target_id": "note-1", "state": "SUCCESS"}],
            "incremental_data": [{"record_type": "operation", "record_id": "event-1"}],
        },
        process_starter=lambda _command, **_kwargs: type("Process", (), {"pid": 4321})(),
    )
    transport = DirectWorkerTransport(worker)
    batches = []
    if batch:

        def download_many(machine, files, **kwargs):
            batches.append(len(files))
            for remote, destination in files:
                transport.sftp_download(machine, remote, destination, **kwargs)

        transport.sftp_download_many = download_many
    manager = ManagerService(
        store=ManagerStore(tmp_path / "manager"),
        transport=transport,  # type: ignore[arg-type]
    )
    manager.register_machine(
        {
            "machine_id": "PC001",
            "ssh_host": "127.0.0.1",
            "ssh_port": 22,
            "ssh_user": "EDY",
            "working_directory": str(tmp_path),
            "python_executable": "python",
            "allowed_versions": ["0.1.0"],
            "expected_role": "executor",
            "task_inbox_root": str(worker.root / "inbox"),
            "task_outbox_root": str(worker.root / "task-outbox"),
        }
    )
    assert manager.check_machine("PC001")["status"] == "READY"
    package = tmp_path / "EXPLORE-01"
    package.mkdir()
    write_function_package_files(package, marker="e2e")
    package.joinpath("manifest.json").write_text(
        json.dumps(
            manifest(
                "function_package",
                "EXPLORE-01",
                "1.0",
                allowed_versions=["0.1.0"],
            )
        ),
        encoding="utf-8",
    )
    transferred = manager.transfer(
        {
            "transfer_id": "transfer-e2e",
            "machine_id": "PC001",
            "transfer_type": "function_package",
            "source_path": str(package.resolve()),
            "allowed_versions": ["0.1.0"],
        }
    )
    dispatched = manager.dispatch_task(
        {
            "task_id": "task-e2e",
            "machine_id": "PC001",
            "account_slot": "alpha",
            "capability": "search-feeds",
            "request_summary": "搜索露营",
            "parameters": {"keyword": "露营"},
            "allowed_versions": ["0.1.0"],
        }
    )
    manager.store.set_collection_cursor("PC001", 99)
    remote_execution_id = dispatched["task"]["remote_execution_id"]
    worker.send_task_progress("task-e2e", remote_execution_id=remote_execution_id)
    worker.run_task("task-e2e", remote_execution_id=remote_execution_id)
    worker.push_task_result("task-e2e", remote_execution_id=remote_execution_id)
    collected = manager.collect_results("PC001", download_evidence=False)

    assert transferred["status"] == "SUCCESS"
    assert transferred["transfer"]["actual_version"] == "1.0"
    assert dispatched["status"] == "TASK_ACK"
    assert dispatched["task"]["dispatch_state"] == "ACKNOWLEDGED"
    assert collected["previous_cursor"] == 0
    wake_call = next(item for item in transport.calls if item[0] == "wake-task")
    assert set(wake_call[1]) == {"task_id", "file_name"}
    assert wake_call[1]["task_id"] == "task-e2e"
    assert collected["collected"] == 1
    assert collected["next_cursor"] == 4
    assert manager.store.get_collection_cursor("PC001:sftp_outbox") == 4
    assert collected["transport"] == "sftp_outbox"
    collection = collected["outbox_collection"]
    assert collection["total_records"] == collection["imported_records"] == 4
    assert collection["pending_records"] == 0
    assert len(list(Path(collection["archive_root"]).rglob("*.json"))) == 4
    if batch:
        assert batches == [4]
    repeated = manager.collect_results("PC001", download_evidence=False)
    assert repeated["collected"] == 0
    assert repeated["next_cursor"] == 4
    assert repeated["outbox_collection"]["batch_records"] == 0
    assert repeated["outbox_collection"]["imported_records"] == 4
    assert not any(item[0] == "collect-results" for item in transport.calls)
    assert "RESULT_ACK" in {
        item["message_type"] for item in manager.list_agent_messages()["agent_messages"]
    }


def test_fixed_json_entry_routes_actions_without_account_execution(tmp_path) -> None:
    service = make_service(tmp_path / "worker")
    request = io.StringIO(json.dumps({"action": "health"}))
    response = io.StringIO()

    exit_code = main(
        stdin=request,
        stdout=response,
        service_factory=lambda: service,
    )
    payload = json.loads(response.getvalue())
    accepted = dispatch_request(service, {"action": "accept-task", **task_request()})

    assert exit_code == 0
    assert payload["health"]["status"] == "READY"
    assert accepted["execution"]["state"] == "ACCEPTED"


def test_fixed_entry_decodes_utf8_stdin_even_when_windows_stdio_defaults_to_gbk(
    tmp_path,
) -> None:
    request = {
        "action": "configure",
        "payload": {
            "machine_id": "PC001",
            "role": "executor",
            "project_version": "1.5.0",
            "package_root": str(tmp_path / "中文功能包"),
            "task_inbox_root": str(tmp_path / "中文任务"),
        },
    }
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "gbk"
    environment["XHS_MANAGER_WORKER_HOME"] = str(tmp_path / "worker")
    worker = Path(__file__).parents[1] / "scripts" / "manager_worker.py"

    completed = subprocess.run(
        [sys.executable, str(worker)],
        input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        check=False,
        env=environment,
        timeout=30,
    )

    assert completed.returncode == 0
    response = json.loads(completed.stdout.decode("utf-8"))
    assert "中文功能包" in response["config"]["package_root"]
    assert "中文任务" in response["config"]["task_inbox_root"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (TimeoutError("timed out"), "LOCAL_SERVICE_TIMEOUT"),
        (ConnectionRefusedError("refused"), "LOCAL_SERVICE_UNAVAILABLE"),
    ],
)
def test_local_api_distinguishes_timeout_from_connection_failure(
    tmp_path, monkeypatch, error, expected
):
    import manager_worker_service as module

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(module, "urlopen", fail)
    service = make_service(tmp_path / "worker")
    with pytest.raises(WorkerServiceError) as caught:
        service._local_api_request("GET", "/session")
    assert caught.value.code == expected


def test_profile_timeout_records_one_attempt_without_retry(tmp_path, monkeypatch):
    service = make_service(tmp_path / "worker")
    calls = []

    def local_api(method, path, *args, **kwargs):
        calls.append(path)
        if path == "/session":
            return {"session_token": "test-session"}
        raise WorkerServiceError("LOCAL_SERVICE_TIMEOUT", "请求超时")

    monkeypatch.setattr(service, "_local_api_request", local_api)
    with pytest.raises(WorkerServiceError) as caught:
        service.account_action("alpha", "profile-start")
    assert caught.value.code == "PROFILE_START_TIMEOUT"
    assert calls == ["/session", "/accounts/alpha/setup/begin"]
    assert len(service._store.snapshot()["profile_startups"]) == 1


def test_profile_start_reports_extension_wait_result(tmp_path, monkeypatch):
    service = make_service(tmp_path / "worker")

    def local_api(method, path, *args, **kwargs):
        if path == "/session":
            return {"session_token": "test-session"}
        return {
            "setup": {
                "phase": "WAITING_EXTENSION",
                "lifecycle": {
                    "ready": False,
                    "error_code": "EXTENSION_NOT_CONNECTED",
                    "message": "Profile 已打开，扩展仍未连接",
                    "timing": {"total_seconds": 15.2, "mode": "launch"},
                },
            }
        }

    monkeypatch.setattr(service, "_local_api_request", local_api)
    monkeypatch.setattr(
        service,
        "account_status",
        lambda account: {
            "checked_at": "2026-09-08",
            "account": {"name": account, "status": "UNKNOWN"},
        },
    )
    result = service.account_action("alpha", "profile-start")
    assert result["success"] is False
    assert result["error_code"] == "EXTENSION_NOT_CONNECTED"
    assert "扩展仍未连接" in result["message"]
    record = service._store.snapshot()["profile_startups"][-1]
    assert record["ready"] is False
    assert record["timing"]["total_seconds"] == 15.2
