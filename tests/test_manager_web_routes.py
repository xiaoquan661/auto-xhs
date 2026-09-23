from __future__ import annotations

import pytest

from scripts.application_service import ServiceError
from scripts.web_server import _dispatch_api, _dispatch_mutation


class FakeApplicationService:
    def health(self) -> dict:
        return {"success": True, "status": "ok"}


class FakeManagerService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def _result(self, name: str, *values: object) -> dict:
        self.calls.append((name, *values))
        return {"success": True, "operation": name}

    def list_machines(self) -> dict:
        return self._result("list_machines")

    def dashboard_snapshot(self) -> dict:
        return self._result("dashboard_snapshot")

    def dashboard_details(self, section: str = "all") -> dict:
        return self._result("dashboard_details", section)

    def list_accounts(self) -> dict:
        return self._result("list_accounts")

    def list_account_alerts(self) -> dict:
        return self._result("list_account_alerts")

    def list_sync_status(self) -> dict:
        return self._result("list_sync_status")

    def list_personas(self) -> dict:
        return self._result("list_personas")

    def get_persona(self, persona_id: str) -> dict:
        return self._result("get_persona", persona_id)

    def list_goals(self) -> dict:
        return self._result("list_goals")

    def get_goal(self, goal_id: str) -> dict:
        return self._result("get_goal", goal_id)

    def list_operation_tasks(self) -> dict:
        return self._result("list_operation_tasks")

    def list_agent_messages(self) -> dict:
        return self._result("list_agent_messages")

    def get_operation_task(self, operation_task_id: str) -> dict:
        return self._result("get_operation_task", operation_task_id)

    def list_machine_accounts(self, machine_id: str) -> dict:
        return self._result("list_machine_accounts", machine_id)

    def get_machine_account_status(self, machine_id: str, account: str) -> dict:
        return self._result("get_machine_account_status", machine_id, account)

    def list_tasks(self) -> dict:
        return self._result("list_tasks")

    def list_schedules(self) -> dict:
        return self._result("list_schedules")

    def list_operation_reports(self) -> dict:
        return self._result("list_operation_reports")

    def list_transfers(self) -> dict:
        return self._result("list_transfers")

    def list_results(self) -> dict:
        return self._result("list_results")

    def get_task(self, task_id: str) -> dict:
        return self._result("get_task", task_id)

    def list_function_versions(
        self,
        machine_id: str,
        function_id: str | None = None,
    ) -> dict:
        return self._result("list_function_versions", machine_id, function_id)

    def register_machine(self, body: dict) -> dict:
        return self._result("register_machine", body)

    def save_persona(self, body: dict) -> dict:
        return self._result("save_persona", body)

    def save_goal(self, body: dict) -> dict:
        return self._result("save_goal", body)

    def create_operation_task(self, body: dict) -> dict:
        return self._result("create_operation_task", body)

    def claim_operation_task(self, operation_task_id: str, body: dict) -> dict:
        return self._result("claim_operation_task", operation_task_id, body)

    def record_operation_plan(self, operation_task_id: str, body: dict) -> dict:
        return self._result("record_operation_plan", operation_task_id, body)

    def finish_operation_task(self, operation_task_id: str, body: dict) -> dict:
        return self._result("finish_operation_task", operation_task_id, body)

    def activate_goal(self, goal_id: str) -> dict:
        return self._result("activate_goal", goal_id)

    def pause_goal(self, goal_id: str) -> dict:
        return self._result("pause_goal", goal_id)

    def complete_goal(self, goal_id: str) -> dict:
        return self._result("complete_goal", goal_id)

    def check_machine(self, machine_id: str) -> dict:
        return self._result("check_machine", machine_id)

    def machine_executor_status(self, machine_id: str) -> dict:
        return self._result("machine_executor_status", machine_id)

    def start_machine_executor(self, machine_id: str) -> dict:
        return self._result("start_machine_executor", machine_id)

    def restart_machine_executor(self, machine_id: str) -> dict:
        return self._result("restart_machine_executor", machine_id)

    def machine_webui_status(self, machine_id: str) -> dict:
        return self._result("machine_webui_status", machine_id)

    def start_machine_webui(self, machine_id: str) -> dict:
        return self._result("start_machine_webui", machine_id)

    def restart_machine_webui(self, machine_id: str) -> dict:
        return self._result("restart_machine_webui", machine_id)

    def doctor_machine_account(self, machine_id: str, account: str) -> dict:
        return self._result("doctor_machine_account", machine_id, account)

    def run_machine_account_action(
        self,
        machine_id: str,
        account: str,
        action: str,
    ) -> dict:
        return self._result("run_machine_account_action", machine_id, account, action)

    def update_account_management(
        self,
        machine_id: str,
        account: str,
        body: dict,
    ) -> dict:
        return self._result("update_account_management", machine_id, account, body)

    def request_machine_account_field_action(
        self,
        machine_id: str,
        account: str,
        body: dict,
    ) -> dict:
        return self._result(
            "request_machine_account_field_action",
            machine_id,
            account,
            body,
        )

    def pause_machine(self, machine_id: str) -> dict:
        return self._result("pause_machine", machine_id)

    def resume_machine(self, machine_id: str) -> dict:
        return self._result("resume_machine", machine_id)

    def collect_results(self, machine_id: str, *, download_evidence: bool = True) -> dict:
        return self._result("collect_results", machine_id, download_evidence)

    def dispatch_task(self, body: dict) -> dict:
        return self._result("dispatch_task", body)

    def register_schedule(self, body: dict) -> dict:
        return self._result("register_schedule", body)

    def generate_operation_report(self, body: dict) -> dict:
        return self._result("generate_operation_report", body)

    def pause_schedule(self, schedule_id: str) -> dict:
        return self._result("pause_schedule", schedule_id)

    def resume_schedule(self, schedule_id: str) -> dict:
        return self._result("resume_schedule", schedule_id)

    def record_schedule_run(self, schedule_id: str, body: dict) -> dict:
        return self._result("record_schedule_run", schedule_id, body)

    def revise_operation_report(self, report_id: str, body: dict) -> dict:
        return self._result("revise_operation_report", report_id, body)

    def reconcile_task(self, task_id: str) -> dict:
        return self._result("reconcile_task", task_id)

    def pause_task(self, task_id: str) -> dict:
        return self._result("pause_task", task_id)

    def resume_task(self, task_id: str) -> dict:
        return self._result("resume_task", task_id)

    def transfer(self, body: dict) -> dict:
        return self._result("transfer", body)

    def set_function_version_policy(self, body: dict) -> dict:
        return self._result("set_function_version_policy", body)


@pytest.mark.parametrize(
    ("path", "expected_call"),
    [
        ("/api/v1/manager/dashboard-snapshot", ("dashboard_snapshot",)),
        ("/api/v1/manager/dashboard-details/audit", ("dashboard_details", "audit")),
        ("/api/v1/manager/dashboard-details/messages", ("dashboard_details", "messages")),
        ("/api/v1/manager/dashboard-details/debug", ("dashboard_details", "debug")),
        ("/api/v1/manager/machines", ("list_machines",)),
        ("/api/v1/manager/accounts", ("list_accounts",)),
        ("/api/v1/manager/alerts", ("list_account_alerts",)),
        ("/api/v1/manager/sync-status", ("list_sync_status",)),
        ("/api/v1/manager/personas", ("list_personas",)),
        ("/api/v1/manager/personas/persona%201", ("get_persona", "persona 1")),
        ("/api/v1/manager/goals", ("list_goals",)),
        ("/api/v1/manager/goals/goal%201", ("get_goal", "goal 1")),
        ("/api/v1/manager/operation-tasks", ("list_operation_tasks",)),
        ("/api/v1/manager/agent-messages", ("list_agent_messages",)),
        (
            "/api/v1/manager/operation-tasks/operation%201",
            ("get_operation_task", "operation 1"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts",
            ("list_machine_accounts", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts/brand-a/status",
            ("get_machine_account_status", "EXECUTOR-01", "brand-a"),
        ),
        ("/api/v1/manager/tasks", ("list_tasks",)),
        ("/api/v1/manager/schedules", ("list_schedules",)),
        ("/api/v1/manager/operation-reports", ("list_operation_reports",)),
        ("/api/v1/manager/transfers", ("list_transfers",)),
        ("/api/v1/manager/results", ("list_results",)),
        ("/api/v1/manager/tasks/task%201", ("get_task", "task 1")),
        (
            "/api/v1/manager/machines/EXECUTOR-01/versions",
            ("list_function_versions", "EXECUTOR-01", None),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/versions/EXPLORE-02",
            ("list_function_versions", "EXECUTOR-01", "EXPLORE-02"),
        ),
    ],
)
def test_manager_get_routes(path: str, expected_call: tuple) -> None:
    manager = FakeManagerService()

    result = _dispatch_api(FakeApplicationService(), path, manager)  # type: ignore[arg-type]

    assert result["operation"] == expected_call[0]
    assert manager.calls == [expected_call]


@pytest.mark.parametrize(
    ("path", "body", "expected_call"),
    [
        (
            "/api/v1/manager/machines",
            {"machine_id": "EXECUTOR-01"},
            ("register_machine", {"machine_id": "EXECUTOR-01"}),
        ),
        (
            "/api/v1/manager/personas",
            {"persona_id": "PERSONA-1"},
            ("save_persona", {"persona_id": "PERSONA-1"}),
        ),
        (
            "/api/v1/manager/goals",
            {"goal_id": "GOAL-1"},
            ("save_goal", {"goal_id": "GOAL-1"}),
        ),
        (
            "/api/v1/manager/goals/GOAL-1/activate",
            {},
            ("activate_goal", "GOAL-1"),
        ),
        (
            "/api/v1/manager/goals/GOAL-1/pause",
            {},
            ("pause_goal", "GOAL-1"),
        ),
        (
            "/api/v1/manager/goals/GOAL-1/complete",
            {},
            ("complete_goal", "GOAL-1"),
        ),
        (
            "/api/v1/manager/operation-tasks",
            {"goal_id": "GOAL-1"},
            ("create_operation_task", {"goal_id": "GOAL-1"}),
        ),
        (
            "/api/v1/manager/operation-tasks/OPERATION-1/claim",
            {"manager_agent_id": "manager-agent-01"},
            (
                "claim_operation_task",
                "OPERATION-1",
                {"manager_agent_id": "manager-agent-01"},
            ),
        ),
        (
            "/api/v1/manager/operation-tasks/OPERATION-1/plan",
            {"manager_agent_id": "manager-agent-01"},
            (
                "record_operation_plan",
                "OPERATION-1",
                {"manager_agent_id": "manager-agent-01"},
            ),
        ),
        (
            "/api/v1/manager/operation-tasks/OPERATION-1/finish",
            {"manager_agent_id": "manager-agent-01", "state": "COMPLETED"},
            (
                "finish_operation_task",
                "OPERATION-1",
                {"manager_agent_id": "manager-agent-01", "state": "COMPLETED"},
            ),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/check",
            {},
            ("check_machine", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts/brand-a/doctor",
            {},
            ("doctor_machine_account", "EXECUTOR-01", "brand-a"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts/brand-a/bridge/restart",
            {},
            ("run_machine_account_action", "EXECUTOR-01", "brand-a", "bridge-restart"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts/brand-a/profile/start",
            {},
            ("run_machine_account_action", "EXECUTOR-01", "brand-a", "profile-start"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts/brand-a/management",
            {"account_note": "主账号", "scheduling_enabled": True},
            (
                "update_account_management",
                "EXECUTOR-01",
                "brand-a",
                {"account_note": "主账号", "scheduling_enabled": True},
            ),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/accounts/brand-a/field-request",
            {"request_type": "LOGIN", "instructions": "请现场登录"},
            (
                "request_machine_account_field_action",
                "EXECUTOR-01",
                "brand-a",
                {"request_type": "LOGIN", "instructions": "请现场登录"},
            ),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/executor-status",
            {},
            ("machine_executor_status", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/executor-start",
            {},
            ("start_machine_executor", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/executor-restart",
            {},
            ("restart_machine_executor", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/webui-status",
            {},
            ("machine_webui_status", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/webui-start",
            {},
            ("start_machine_webui", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/webui-restart",
            {},
            ("restart_machine_webui", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/pause",
            {},
            ("pause_machine", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/resume",
            {},
            ("resume_machine", "EXECUTOR-01"),
        ),
        (
            "/api/v1/manager/machines/EXECUTOR-01/collect",
            {"download_evidence": False},
            ("collect_results", "EXECUTOR-01", False),
        ),
        (
            "/api/v1/manager/tasks",
            {"task_id": "task-1"},
            ("dispatch_task", {"task_id": "task-1"}),
        ),
        (
            "/api/v1/manager/schedules",
            {"schedule_id": "schedule-1"},
            ("register_schedule", {"schedule_id": "schedule-1"}),
        ),
        (
            "/api/v1/manager/schedules/schedule-1/pause",
            {},
            ("pause_schedule", "schedule-1"),
        ),
        (
            "/api/v1/manager/schedules/schedule-1/resume",
            {},
            ("resume_schedule", "schedule-1"),
        ),
        (
            "/api/v1/manager/schedules/schedule-1/runs",
            {"task_id": "task-1", "state": "SUCCESS"},
            (
                "record_schedule_run",
                "schedule-1",
                {"task_id": "task-1", "state": "SUCCESS"},
            ),
        ),
        (
            "/api/v1/manager/operation-reports",
            {"report_type": "daily"},
            ("generate_operation_report", {"report_type": "daily"}),
        ),
        (
            "/api/v1/manager/operation-reports/report-1/revisions",
            {"revision_reason": "补采"},
            ("revise_operation_report", "report-1", {"revision_reason": "补采"}),
        ),
        (
            "/api/v1/manager/tasks/task-1/reconcile",
            {},
            ("reconcile_task", "task-1"),
        ),
        (
            "/api/v1/manager/tasks/task-1/pause",
            {},
            ("pause_task", "task-1"),
        ),
        (
            "/api/v1/manager/tasks/task-1/resume",
            {},
            ("resume_task", "task-1"),
        ),
        (
            "/api/v1/manager/transfers",
            {"transfer_id": "transfer-1"},
            ("transfer", {"transfer_id": "transfer-1"}),
        ),
        (
            "/api/v1/manager/version-policies",
            {"function_id": "EXPLORE-02"},
            ("set_function_version_policy", {"function_id": "EXPLORE-02"}),
        ),
    ],
)
def test_manager_post_routes(path: str, body: dict, expected_call: tuple) -> None:
    manager = FakeManagerService()

    result = _dispatch_mutation(
        FakeApplicationService(),  # type: ignore[arg-type]
        "POST",
        path,
        body,
        manager,  # type: ignore[arg-type]
    )

    assert result["operation"] == expected_call[0]
    assert manager.calls == [expected_call]


def test_manager_routes_require_an_enabled_manager_service() -> None:
    with pytest.raises(ServiceError) as captured:
        _dispatch_api(FakeApplicationService(), "/api/v1/manager/machines")  # type: ignore[arg-type]

    assert captured.value.code == "MANAGER_SERVICE_UNAVAILABLE"
    assert captured.value.http_status == 503


def test_existing_dispatch_signatures_remain_compatible() -> None:
    result = _dispatch_api(FakeApplicationService(), "/api/v1/health")  # type: ignore[arg-type]

    assert result == {"success": True, "status": "ok"}
