from __future__ import annotations

import argparse
import io
import json

import pytest
from service_errors import ServiceError

from scripts import manager_cli


class FakeManagerService:
    def __init__(self, *, fail: bool = False, unsuccessful: bool = False) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.fail = fail
        self.unsuccessful = unsuccessful

    def _record(self, method: str, *args, **kwargs) -> dict:
        self.calls.append((method, args, kwargs))
        if self.fail:
            raise ServiceError("TEST_ERROR", "中文错误")
        return {"success": not self.unsuccessful, "method": method}

    def register_machine(self, values: dict) -> dict:
        return self._record("register_machine", values)

    def list_machines(self) -> dict:
        return self._record("list_machines")

    def check_machine(self, machine_id: str) -> dict:
        return self._record("check_machine", machine_id)

    def pause_machine(self, machine_id: str) -> dict:
        return self._record("pause_machine", machine_id)

    def resume_machine(self, machine_id: str) -> dict:
        return self._record("resume_machine", machine_id)

    def save_persona(self, values: dict) -> dict:
        return self._record("save_persona", values)

    def list_personas(self) -> dict:
        return self._record("list_personas")

    def get_persona(self, persona_id: str) -> dict:
        return self._record("get_persona", persona_id)

    def save_goal(self, values: dict) -> dict:
        return self._record("save_goal", values)

    def list_goals(self) -> dict:
        return self._record("list_goals")

    def get_goal(self, goal_id: str) -> dict:
        return self._record("get_goal", goal_id)

    def activate_goal(self, goal_id: str) -> dict:
        return self._record("activate_goal", goal_id)

    def pause_goal(self, goal_id: str) -> dict:
        return self._record("pause_goal", goal_id)

    def complete_goal(self, goal_id: str) -> dict:
        return self._record("complete_goal", goal_id)

    def create_operation_task(self, values: dict) -> dict:
        return self._record("create_operation_task", values)

    def list_operation_tasks(self, state: str | None = None) -> dict:
        return self._record("list_operation_tasks", state)

    def get_operation_task(self, operation_task_id: str) -> dict:
        return self._record("get_operation_task", operation_task_id)

    def claim_operation_task(self, operation_task_id: str, values: dict) -> dict:
        return self._record("claim_operation_task", operation_task_id, values)

    def record_operation_plan(self, operation_task_id: str, values: dict) -> dict:
        return self._record("record_operation_plan", operation_task_id, values)

    def finish_operation_task(self, operation_task_id: str, values: dict) -> dict:
        return self._record("finish_operation_task", operation_task_id, values)

    def dispatch_task(self, values: dict) -> dict:
        return self._record("dispatch_task", values)

    def list_tasks(self) -> dict:
        return self._record("list_tasks")

    def get_task(self, task_id: str, *, refresh: bool = False) -> dict:
        return self._record("get_task", task_id, refresh=refresh)

    def pause_task(self, task_id: str) -> dict:
        return self._record("pause_task", task_id)

    def resume_task(self, task_id: str) -> dict:
        return self._record("resume_task", task_id)

    def reconcile_task(self, task_id: str) -> dict:
        return self._record("reconcile_task", task_id)

    def register_schedule(self, values: dict) -> dict:
        return self._record("register_schedule", values)

    def list_schedules(self) -> dict:
        return self._record("list_schedules")

    def pause_schedule(self, schedule_id: str) -> dict:
        return self._record("pause_schedule", schedule_id)

    def resume_schedule(self, schedule_id: str) -> dict:
        return self._record("resume_schedule", schedule_id)

    def record_schedule_run(self, schedule_id: str, values: dict) -> dict:
        return self._record("record_schedule_run", schedule_id, values)

    def generate_operation_report(self, values: dict) -> dict:
        return self._record("generate_operation_report", values)

    def list_operation_reports(self) -> dict:
        return self._record("list_operation_reports")

    def revise_operation_report(self, report_id: str, values: dict) -> dict:
        return self._record("revise_operation_report", report_id, values)

    def transfer(self, values: dict) -> dict:
        return self._record("transfer", values)

    def list_function_versions(
        self,
        machine_id: str,
        function_id: str | None = None,
    ) -> dict:
        return self._record("list_function_versions", machine_id, function_id)

    def set_function_version_policy(self, values: dict) -> dict:
        return self._record("set_function_version_policy", values)

    def collect_results(self, machine_id: str, *, download_evidence: bool = True) -> dict:
        return self._record(
            "collect_results",
            machine_id,
            download_evidence=download_evidence,
        )

    def list_results(self, machine_id: str | None = None) -> dict:
        return self._record("list_results", machine_id)


def test_parser_supports_nested_manager_commands() -> None:
    parsed = manager_cli.build_parser().parse_args(
        ["task", "status", "--task-id", "task-1", "--refresh"]
    )

    assert parsed.resource == "task"
    assert parsed.action == "status"
    assert parsed.task_id == "task-1"
    assert parsed.refresh is True


def test_load_json_object_requires_absolute_utf8_object(tmp_path) -> None:
    source = tmp_path / "机器.json"
    source.write_text(
        json.dumps({"machine_id": "执行机-01"}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert manager_cli.load_json_object(str(source)) == {"machine_id": "执行机-01"}
    with pytest.raises(ServiceError) as relative:
        manager_cli.load_json_object("machine.json")
    assert relative.value.code == "INPUT_PATH_NOT_ABSOLUTE"

    source.write_text("[]", encoding="utf-8")
    with pytest.raises(ServiceError) as not_object:
        manager_cli.load_json_object(str(source))
    assert not_object.value.code == "INPUT_JSON_NOT_OBJECT"


def test_load_json_object_reports_missing_and_invalid_files(tmp_path) -> None:
    with pytest.raises(ServiceError) as missing:
        manager_cli.load_json_object(str(tmp_path / "missing.json"))
    assert missing.value.code == "INPUT_FILE_NOT_FOUND"

    source = tmp_path / "invalid.json"
    source.write_text("{", encoding="utf-8")
    with pytest.raises(ServiceError) as invalid:
        manager_cli.load_json_object(str(source))
    assert invalid.value.code == "INVALID_INPUT_JSON"


def test_create_service_uses_manager_and_openssh_homes(tmp_path, monkeypatch) -> None:
    manager_home = tmp_path / "manager"
    openssh_home = tmp_path / "OpenSSH"
    captured: dict = {}
    store = object()
    transport = object()

    def make_store(*, root=None):
        captured["manager_home"] = root
        return store

    def make_transport(*, environ):
        captured["openssh_home"] = environ.get("XHS_OPENSSH_HOME")
        return transport

    def make_service(*, store, transport):
        captured["store"] = store
        captured["transport"] = transport
        return object()

    monkeypatch.setattr(manager_cli, "ManagerStore", make_store)
    monkeypatch.setattr(manager_cli, "OpenSSHTransport", make_transport)
    monkeypatch.setattr(manager_cli, "ManagerService", make_service)
    args = argparse.Namespace(
        manager_home=str(manager_home),
        openssh_home=str(openssh_home),
    )

    service = manager_cli.create_service(args)

    assert service is not None
    assert captured["manager_home"] == str(manager_home.resolve())
    assert captured["openssh_home"] == str(openssh_home.resolve())
    assert captured["store"] is store
    assert captured["transport"] is transport


def test_all_commands_route_to_manager_service(tmp_path, capsys) -> None:
    request = tmp_path / "request.json"
    request.write_text('{"编号": "值"}', encoding="utf-8")
    service = FakeManagerService()

    def factory(_args: argparse.Namespace) -> FakeManagerService:
        return service

    cases = [
        (["machine", "register", "--input-file", str(request)], "register_machine"),
        (["machine", "list"], "list_machines"),
        (["machine", "check", "--machine-id", "exec-1"], "check_machine"),
        (["machine", "pause", "--machine-id", "exec-1"], "pause_machine"),
        (["machine", "resume", "--machine-id", "exec-1"], "resume_machine"),
        (["persona", "save", "--input-file", str(request)], "save_persona"),
        (["persona", "list"], "list_personas"),
        (["persona", "show", "--persona-id", "persona-1"], "get_persona"),
        (["goal", "save", "--input-file", str(request)], "save_goal"),
        (["goal", "list"], "list_goals"),
        (["goal", "show", "--goal-id", "goal-1"], "get_goal"),
        (["goal", "activate", "--goal-id", "goal-1"], "activate_goal"),
        (["goal", "pause", "--goal-id", "goal-1"], "pause_goal"),
        (["goal", "complete", "--goal-id", "goal-1"], "complete_goal"),
        (
            ["operation-task", "submit", "--input-file", str(request)],
            "create_operation_task",
        ),
        (["operation-task", "list", "--state", "WAITING_AGENT"], "list_operation_tasks"),
        (
            ["operation-task", "show", "--operation-task-id", "operation-1"],
            "get_operation_task",
        ),
        (
            [
                "operation-task",
                "claim",
                "--operation-task-id",
                "operation-1",
                "--agent-id",
                "manager-agent-01",
            ],
            "claim_operation_task",
        ),
        (
            [
                "operation-task",
                "plan",
                "--operation-task-id",
                "operation-1",
                "--input-file",
                str(request),
            ],
            "record_operation_plan",
        ),
        (
            [
                "operation-task",
                "finish",
                "--operation-task-id",
                "operation-1",
                "--input-file",
                str(request),
            ],
            "finish_operation_task",
        ),
        (["task", "dispatch", "--request-file", str(request)], "dispatch_task"),
        (["task", "list"], "list_tasks"),
        (["task", "status", "--task-id", "task-1", "--refresh"], "get_task"),
        (["task", "pause", "--task-id", "task-1"], "pause_task"),
        (["task", "resume", "--task-id", "task-1"], "resume_task"),
        (["task", "reconcile", "--task-id", "task-1"], "reconcile_task"),
        (["schedule", "register", "--input-file", str(request)], "register_schedule"),
        (["schedule", "list"], "list_schedules"),
        (["schedule", "pause", "--schedule-id", "schedule-1"], "pause_schedule"),
        (["schedule", "resume", "--schedule-id", "schedule-1"], "resume_schedule"),
        (
            [
                "schedule",
                "record-run",
                "--schedule-id",
                "schedule-1",
                "--input-file",
                str(request),
            ],
            "record_schedule_run",
        ),
        (
            ["operation-report", "generate", "--input-file", str(request)],
            "generate_operation_report",
        ),
        (["operation-report", "list"], "list_operation_reports"),
        (
            [
                "operation-report",
                "revise",
                "--report-id",
                "report-1",
                "--input-file",
                str(request),
            ],
            "revise_operation_report",
        ),
        (["transfer", "--input-file", str(request)], "transfer"),
        (
            [
                "version",
                "list",
                "--machine-id",
                "exec-1",
                "--function-id",
                "EXPLORE-02",
            ],
            "list_function_versions",
        ),
        (
            ["version", "set", "--input-file", str(request)],
            "set_function_version_policy",
        ),
        (
            ["collect", "--machine-id", "exec-1", "--no-download-evidence"],
            "collect_results",
        ),
        (["result", "list", "--machine-id", "exec-1"], "list_results"),
    ]

    for argv, expected_method in cases:
        assert manager_cli.main(argv, service_factory=factory) == 0
        assert service.calls[-1][0] == expected_method

    assert next(call for call in service.calls if call[0] == "get_task") == (
        "get_task",
        ("task-1",),
        {"refresh": True},
    )
    assert next(call for call in service.calls if call[0] == "collect_results") == (
        "collect_results",
        ("exec-1",),
        {"download_evidence": False},
    )
    assert next(call for call in service.calls if call[0] == "list_results") == (
        "list_results",
        ("exec-1",),
        {},
    )
    assert service.calls[0][1][0] == {"编号": "值"}
    assert capsys.readouterr().out


def test_error_output_is_utf8_json_and_returns_exit_code_two(capsys) -> None:
    service = FakeManagerService(fail=True)

    exit_code = manager_cli.main(
        ["machine", "list"],
        service_factory=lambda _args: service,
    )

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "中文错误" in output
    assert "\\u4e2d" not in output
    assert json.loads(output) == {
        "success": False,
        "error": {"code": "TEST_ERROR", "message": "中文错误"},
    }


def test_output_reconfigures_non_utf8_console(monkeypatch) -> None:
    raw_output = io.BytesIO()
    console = io.TextIOWrapper(raw_output, encoding="gbk")
    monkeypatch.setattr(manager_cli.sys, "stdout", console)

    manager_cli._output({"message": "完成 😊"})
    console.flush()

    assert json.loads(raw_output.getvalue().decode("utf-8")) == {"message": "完成 😊"}


def test_unsuccessful_service_result_returns_exit_code_two(capsys) -> None:
    service = FakeManagerService(unsuccessful=True)

    exit_code = manager_cli.main(
        ["machine", "list"],
        service_factory=lambda _args: service,
    )

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["success"] is False


def test_parser_errors_are_returned_as_json(capsys) -> None:
    exit_code = manager_cli.main(["machine", "check"])

    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["error"]["code"] == "INVALID_ARGUMENT"
