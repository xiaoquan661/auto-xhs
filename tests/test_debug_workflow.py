from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from debug_common import code_path, incident_from_result, repair_relative_path
from manager_debug import ManagerDebugService
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_transport import ResultUnknownError
from manager_worker import dispatch_request
from manager_worker_service import ManagerWorkerService
from service_errors import ServiceError
from worker_debug import WorkerDebugService


class LocalTransport:
    def __init__(self, worker: ManagerWorkerService, project: Path) -> None:
        self.worker = worker
        self.debug = WorkerDebugService(worker, project)
        self.timeout_after_install = False
        self.healthy = True

    def invoke(self, machine: dict, action: str, payload: dict, **kwargs: object) -> dict:
        if action.startswith("debug-"):
            result = self.debug.action(action.removeprefix("debug-"), payload)
            if action == "debug-apply" and self.timeout_after_install:
                raise ResultUnknownError("SSH 响应中断")
            return result
        if action == "restart-executor":
            return {"success": True}
        if action == "health":
            health = {"executor_service": {"ready": self.healthy}}
            layout = self.debug.project / "source-layout.json"
            if layout.is_file():
                health["source_layout"] = json.loads(layout.read_text(encoding="utf-8"))
            return {"success": True, "health": health}
        return dispatch_request(self.worker, {"action": action, "payload": payload})

    def upload(self, machine: dict, source: str, destination: str, **kwargs: object) -> None:
        shutil.copytree(source, destination)

    def sftp_download(
        self, machine: dict, source: str, destination: Path, **kwargs: object
    ) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)

    def sftp_download_many(
        self, machine: dict, files: list[tuple[str, Path]], **kwargs: object
    ) -> None:
        for source, destination in files:
            self.sftp_download(machine, source, destination)


@pytest.fixture
def workflow(tmp_path: Path) -> tuple:
    project = tmp_path / "source"
    employee = tmp_path / "employee"
    for root, value in ((project, 2), (employee, 1)):
        (root / "scripts").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "scripts" / "sample.py").write_text(f"VALUE = {value}\n", encoding="utf-8")
        (root / "tests" / "test_sample.py").write_text(
            "import runpy\nfrom pathlib import Path\n"
            "def test_regression():\n"
            "    source = Path(__file__).parents[1] / 'scripts/sample.py'\n"
            "    assert runpy.run_path(str(source))['VALUE'] == 2\n",
            encoding="utf-8",
        )
    worker = ManagerWorkerService(
        tmp_path / "worker",
        account_summary_reader=lambda: [],
        local_control=lambda *_: {"success": True},
    )
    worker.configure(machine_id="PC057")
    transport = LocalTransport(worker, employee)
    manager = ManagerService(store=ManagerStore(tmp_path / "manager"), transport=transport)
    manager.store.put(
        "machines",
        "PC057",
        {
            "machine_id": "PC057",
            "status": "READY",
            "working_directory": str(employee),
            "task_outbox_root": worker._store.snapshot()["config"]["task_outbox_root"],
            "task_inbox_root": worker._store.snapshot()["config"]["task_inbox_root"],
            "task_transport": "sftp",
        },
    )
    debug = ManagerDebugService(manager)
    incident = debug.ingest_incident(
        "PC057",
        {
            "summary": "计算结果错误",
            "account_slot": "alpha",
            "source_key": "sample-failure",
        },
    )
    debug.action(
        "diagnose",
        {
            "incident_id": incident["incident_id"],
            "reproduction": "运行 sample 测试",
            "root_cause": "返回值错误",
            "resolution": "修正返回值并添加回归",
        },
    )
    return manager, worker, debug, transport, project, employee, incident


def build(workflow: tuple) -> dict:
    _, _, debug, _, project, _, incident = workflow
    return debug.action(
        "build",
        {
            "incident_id": incident["incident_id"],
            "project_root": str(project),
            "files": ["scripts/sample.py"],
            "tests": ["tests/test_sample.py"],
        },
    )["release"]


def test_report_outbox_roundtrip_and_replay(workflow: tuple) -> None:
    manager, worker, debug, _, _, _, _ = workflow
    report = worker.debug_action(
        "report",
        {
            "summary": "浏览器断连",
            "account_slot": "alpha",
            "source_key": "disconnected",
        },
    )
    again = worker.debug_action(
        "report",
        {
            "summary": "浏览器断连",
            "account_slot": "alpha",
            "source_key": "disconnected",
        },
    )
    assert again["incident"]["incident_id"] == report["incident"]["incident_id"]
    result = manager._collect_sftp_outbox(manager._get_machine("PC057"), download_evidence=False)
    assert result["success"], result
    manager._collect_sftp_outbox(manager._get_machine("PC057"), download_evidence=False)
    incidents = debug.action("list", {})["incidents"]
    assert len(incidents) == 2
    assert any(row["source_incident_id"] == report["incident"]["incident_id"] for row in incidents)


def test_build_deliver_install_verify_accept_and_rollback(workflow: tuple) -> None:
    _, worker, debug, _, _, employee, incident = workflow
    release = build(workflow)
    key = {"release_id": release["release_id"]}
    assert debug.action("dispatch", key)["release"]["state"] == "DELIVERED"
    with pytest.raises(ServiceError, match="验证"):
        debug.action("accept", {"incident_id": incident["incident_id"], "acceptance_note": "通过"})
    assert debug.action("apply", key)["release"]["state"] == "AWAITING_VERIFICATION"
    assert "2" in (employee / "scripts/sample.py").read_text()
    assert worker._store.snapshot()["machine"]["paused"] is True
    assert debug.action("verify", key)["release"]["state"] == "VERIFIED"
    assert (
        debug.action(
            "accept",
            {
                "incident_id": incident["incident_id"],
                "acceptance_note": "复现用例和服务检查通过",
            },
        )["incident"]["state"]
        == "CLOSED"
    )
    assert debug.action("rollback", key)["release"]["state"] == "ROLLED_BACK"
    assert "1" in (employee / "scripts/sample.py").read_text()


def test_failed_employee_test_restores_files(workflow: tuple) -> None:
    _, _, debug, _, _, employee, _ = workflow
    release = build(workflow)
    # Model an employee-only failure such as a missing local dependency.
    test = Path(release["package_path"]) / "payload/tests/test_sample.py"
    test.write_text("def test_employee_failure():\n    assert False\n", encoding="utf-8")
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    result = debug.action("apply", key)
    assert result["release"]["state"] == "ROLLED_BACK"
    assert "1" in (employee / "scripts/sample.py").read_text()
    assert "test_regression" in (employee / "tests/test_sample.py").read_text()


def test_unknown_install_reconciles_without_reapplying(workflow: tuple) -> None:
    _, _, debug, transport, _, _, _ = workflow
    release = build(workflow)
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    transport.timeout_after_install = True
    assert debug.action("apply", key)["release"]["state"] == "RESULT_UNKNOWN"
    with pytest.raises(ServiceError, match="未知"):
        debug.action("apply", key)
    assert debug.action("reconcile", key)["release"]["state"] == "AWAITING_VERIFICATION"


def test_busy_employee_does_not_modify_code(workflow: tuple) -> None:
    _, worker, debug, _, _, employee, _ = workflow
    release = build(workflow)
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    worker._store.mutate(lambda state: state["executions"].update({"busy": {"state": "RUNNING"}}))
    with pytest.raises(ServiceError, match="运行"):
        debug.action("apply", key)
    assert "1" in (employee / "scripts/sample.py").read_text()


def test_health_failure_prevents_acceptance(workflow: tuple) -> None:
    _, _, debug, transport, _, _, _ = workflow
    release = build(workflow)
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    debug.action("apply", key)
    transport.healthy = False
    with pytest.raises(ServiceError, match="健康"):
        debug.action("verify", key)


def test_unknown_result_and_account_isolation(workflow: tuple) -> None:
    _, _, debug, _, _, _, _ = workflow
    result = {"state": "RESULT_UNKNOWN", "task_id": "t", "account_slot": "alpha"}
    report = incident_from_result("PC057", result)
    assert report is not None and report["reconciliation_required"]
    one = debug.ingest_incident("PC057", report)
    assert debug.ingest_incident("PC057", report)["incident_id"] == one["incident_id"]
    other = incident_from_result("PC057", {**result, "account_slot": "beta"})
    assert other is not None
    assert debug.ingest_incident("PC057", other)["incident_id"] != one["incident_id"]


def test_runtime_assets_cannot_enter_package(workflow: tuple) -> None:
    _, _, debug, _, project, _, incident = workflow
    with pytest.raises(ServiceError, match="修复文件"):
        debug.action(
            "build",
            {
                "incident_id": incident["incident_id"],
                "project_root": str(project),
                "files": ["data/accounts.json"],
                "tests": ["tests/test_sample.py"],
            },
        )


def test_failed_manager_tests_do_not_publish(workflow: tuple) -> None:
    manager, _, debug, _, project, _, incident = workflow
    (project / "scripts/sample.py").write_text("VALUE = 0\n", encoding="utf-8")
    result = debug.action(
        "build",
        {
            "incident_id": incident["incident_id"],
            "project_root": str(project),
            "files": ["scripts/sample.py"],
            "tests": ["tests/test_sample.py"],
        },
    )
    assert result["status"] == "VALIDATION_FAILED"
    assert manager.store.list("debug_releases") == []


def test_cli_and_web_api(workflow: tuple, tmp_path: Path) -> None:
    from manager_cli import main
    from web_server import _dispatch_manager_api, _dispatch_manager_mutation

    manager, _, _, _, _, _, incident = workflow
    assert _dispatch_manager_api(manager, "/api/v1/manager/debug")["incidents"]
    response = _dispatch_manager_mutation(
        manager,
        "POST",
        "/api/v1/manager/debug/claim",
        {
            "incident_id": incident["incident_id"],
        },
    )
    assert Path(response["handoff_path"]).is_file()
    request = tmp_path / "show.json"
    request.write_text(json.dumps({"incident_id": incident["incident_id"]}), encoding="utf-8")
    assert (
        main(["debug", "show", "--input-file", str(request)], service_factory=lambda _: manager)
        == 0
    )


def test_late_outbox_receipts_preserve_verified_state(workflow: tuple) -> None:
    manager, _, debug, _, _, _, incident = workflow
    release = build(workflow)
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    debug.action("apply", key)
    debug.action("verify", key)
    result = manager._collect_sftp_outbox(manager._get_machine("PC057"), download_evidence=False)
    assert result["success"], result
    assert manager.store.get("debug_releases", key["release_id"])["state"] == "VERIFIED"
    assert (
        debug.action("show", {"incident_id": incident["incident_id"]})["incident"]["state"]
        == "AWAITING_ACCEPTANCE"
    )


def test_result_failure_automatically_enters_worker_outbox(tmp_path: Path) -> None:
    from tests.test_manager_worker import make_service, task_request

    worker = make_service(tmp_path, executor=lambda _: {"state": "FAILED", "message": "模块报错"})
    worker.accept_task(task_request())
    worker.run_task(task_id="task-1")
    worker.push_task_result(task_id="task-1")
    state = worker._store.snapshot()
    assert len(state["debug_incidents"]) == 1
    assert any(row["record_type"] == "debug_incident" for row in state["outbox_records"].values())


def test_employee_fixed_entry_reports_task_identity(
    workflow: tuple, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    import sys

    from employee_task_cli import main

    _, worker, _, _, _, _, _ = workflow
    root = worker.root / "agent-runs" / "exec-example"
    root.mkdir(parents=True)
    (root / "task.json").write_text(
        json.dumps(
            {
                "task_id": "task-example",
                "remote_execution_id": "exec-example",
                "account_slot": "alpha",
            }
        ),
        encoding="utf-8",
    )
    request = root / "bug.json"
    request.write_text(
        json.dumps({"summary": "读取失败", "account_slot": "wrong"}), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "employee_task_cli.py",
            "--run-root",
            str(root),
            "report-bug",
            "--input-file",
            str(request),
        ],
    )
    main()
    queued = json.loads(capsys.readouterr().out)
    assert queued["state"] == "QUEUED"
    report = json.loads(Path(queued["report_file"]).read_text(encoding="utf-8"))
    assert report["account_slot"] == "alpha"
    assert report["remote_execution_id"] == "exec-example"
    assert report["evidence_index"]["report_file"] == str(request)


def test_new_files_removed_when_install_rolls_back(workflow: tuple) -> None:
    _, _, debug, _, _, employee, _ = workflow
    (employee / "scripts/sample.py").unlink()
    release = build(workflow)
    package = Path(release["package_path"])
    (package / "payload/tests/test_sample.py").write_text(
        "def test_bad():\n    assert False\n",
        encoding="utf-8",
    )
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    assert debug.action("apply", key)["release"]["state"] == "ROLLED_BACK"
    assert not (employee / "scripts/sample.py").exists()


ROLE_SAMPLE = (
    "ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/"
    "worker/runtime/scripts/sample.py"
)


def use_role_layout(project: Path) -> bytes:
    canonical = project / ROLE_SAMPLE
    canonical.parent.mkdir(parents=True)
    shutil.move(project / "scripts/sample.py", canonical)
    facade = (
        "import runpy\nfrom pathlib import Path\n"
        f"VALUE = runpy.run_path(str(Path(__file__).parents[1] / {ROLE_SAMPLE!r}))['VALUE']\n"
    ).encode()
    (project / "scripts/sample.py").write_bytes(facade)
    (project / "source-layout.json").write_text(
        json.dumps({"schema_version": 1, "module_files": {"sample": ROLE_SAMPLE}}),
        encoding="utf-8",
    )
    return facade


@pytest.mark.parametrize("source_file", ["scripts/sample.py", ROLE_SAMPLE])
def test_role_repair_reads_and_installs_canonical_body(workflow: tuple, source_file: str) -> None:
    _, _, debug, _, project, employee, incident = workflow
    source_facade = use_role_layout(project)
    employee_facade = use_role_layout(employee)
    release = debug.action(
        "build",
        {
            "incident_id": incident["incident_id"],
            "project_root": str(project),
            "files": [source_file],
            "tests": ["tests/test_sample.py"],
        },
    )["release"]
    manifest = release["manifest"]
    assert manifest["requires_source_layout"] == 1
    assert manifest["canonical_files"]["scripts/sample.py"] == ROLE_SAMPLE
    assert (
        Path(release["package_path"]) / "payload/scripts/sample.py"
    ).read_text() == "VALUE = 2\n"
    assert (project / "scripts/sample.py").read_bytes() == source_facade
    key = {"release_id": release["release_id"]}
    assert debug.action("dispatch", key)["release"]["state"] == "DELIVERED"
    assert debug.action("apply", key)["release"]["state"] == "AWAITING_VERIFICATION"
    assert (employee / ROLE_SAMPLE).read_text() == "VALUE = 2\n"
    assert (employee / "scripts/sample.py").read_bytes() == employee_facade
    assert debug.action("verify", key)["release"]["state"] == "VERIFIED"
    assert debug.action("rollback", key)["release"]["state"] == "ROLLED_BACK"
    assert (employee / ROLE_SAMPLE).read_text() == "VALUE = 1\n"
    assert (employee / "scripts/sample.py").read_bytes() == employee_facade


def test_role_repair_dispatch_waits_for_target_layout_installation(workflow: tuple) -> None:
    _, worker, debug, _, project, employee, _ = workflow
    use_role_layout(project)
    release = build(workflow)
    with pytest.raises(ServiceError, match="安装项目职责目录版本"):
        debug.action("dispatch", {"release_id": release["release_id"]})
    assert worker._store.snapshot()["transfers"] == {}
    assert (employee / "scripts/sample.py").read_text() == "VALUE = 1\n"


def test_role_repair_apply_checks_target_layout_before_maintenance(workflow: tuple) -> None:
    manager, worker, _, _, project, employee, _ = workflow
    use_role_layout(project)
    release = build(workflow)
    manager.transfer(
        {
            "transfer_id": release["release_id"],
            "machine_id": "PC057",
            "transfer_type": "repair_package",
            "source_path": release["package_path"],
        }
    )
    with pytest.raises(ServiceError, match="安装项目职责目录版本"):
        WorkerDebugService(worker, employee).apply(
            {"release_id": release["release_id"], "transfer_id": release["release_id"]}
        )
    assert worker._store.snapshot()["machine"]["paused"] is False
    assert (employee / "scripts/sample.py").read_text() == "VALUE = 1\n"


def test_legacy_repair_input_resolves_on_role_layout_worker(workflow: tuple) -> None:
    _, _, debug, _, _, employee, _ = workflow
    facade = use_role_layout(employee)
    release = build(workflow)
    assert "requires_source_layout" not in release["manifest"]
    key = {"release_id": release["release_id"]}
    debug.action("dispatch", key)
    assert debug.action("apply", key)["release"]["state"] == "AWAITING_VERIFICATION"
    assert (employee / ROLE_SAMPLE).read_text() == "VALUE = 2\n"
    assert (employee / "scripts/sample.py").read_bytes() == facade
    debug.action("rollback", key)
    assert (employee / ROLE_SAMPLE).read_text() == "VALUE = 1\n"


@pytest.mark.parametrize(
    "logical, canonical",
    [
        (
            "scripts/start-webui.ps1",
            "ManagerStation/Managers/PC023/function_packages/xiaohongshu-skills/0.1.0/"
            "manager/runtime/scripts/start-webui.ps1",
        ),
        (
            "prompts/reply-v2.md",
            "ManagerStation/Managers/PC023/worker_packages/xiaohongshu-skills/0.1.0/"
            "worker/prompts/reply-v2.md",
        ),
    ],
)
def test_repair_resolves_powershell_and_prompt_bodies(
    tmp_path: Path, logical: str, canonical: str
) -> None:
    original = tmp_path / logical
    original.parent.mkdir(parents=True)
    original.write_text("entry reference", encoding="utf-8")
    body = tmp_path / canonical
    body.parent.mkdir(parents=True)
    body.write_text("body before", encoding="utf-8")
    (tmp_path / "source-layout.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "powershell_files": {"start-webui.ps1": canonical}
                if logical.startswith("scripts/")
                else {},
                "resource_roots": {"prompts": str(Path(canonical).parent.as_posix())}
                if logical.startswith("prompts/")
                else {},
            }
        ),
        encoding="utf-8",
    )
    assert repair_relative_path(tmp_path, canonical) == logical
    assert code_path(tmp_path, canonical).read_text(encoding="utf-8") == "body before"
    code_path(tmp_path, logical).write_text("body after", encoding="utf-8")
    assert body.read_text(encoding="utf-8") == "body after"
    assert original.read_text(encoding="utf-8") == "entry reference"
