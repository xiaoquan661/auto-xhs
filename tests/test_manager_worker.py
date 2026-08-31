from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

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
            "local_service": {
                "ready": True,
                "url": "http://127.0.0.1:8765/api/v1",
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
        },
    }
    assert state_path.read_text(encoding="utf-8") == before
    assert not (service.root / "worker-state.lock").exists()
    stored = json.loads(before)
    assert Path(stored["config"]["package_root"]).is_absolute()


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
    assert health["local_service"]["ready"] is False


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
    assert observed["item_results_status"] == "observed"
    assert observed["item_results"] == []
    assert observed["incremental_data_status"] == "observed"
    assert observed["incremental_data"] == []


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
    assert calls == [("GET", "/session"), ("GET", "/tasks/local-task-1")]
    assert service.collect_results(0)["results"][0]["state"] == local_state


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


def test_manager_service_and_worker_share_health_transfer_task_and_cursor_protocol(
    tmp_path,
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
    )
    transport = DirectWorkerTransport(worker)
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
    collected = manager.collect_results("PC001", download_evidence=False)

    assert transferred["status"] == "SUCCESS"
    assert transferred["transfer"]["actual_version"] == "1.0"
    assert dispatched["status"] == "SUCCESS"
    assert collected["collected"] == 1
    assert collected["next_cursor"] == 1
    collect_call = next(item for item in transport.calls if item[0] == "collect-results")
    assert collect_call[1] == {"since_sequence": 0}


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
