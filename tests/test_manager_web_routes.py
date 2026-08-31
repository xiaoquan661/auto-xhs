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

    def list_tasks(self) -> dict:
        return self._result("list_tasks")

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

    def check_machine(self, machine_id: str) -> dict:
        return self._result("check_machine", machine_id)

    def pause_machine(self, machine_id: str) -> dict:
        return self._result("pause_machine", machine_id)

    def resume_machine(self, machine_id: str) -> dict:
        return self._result("resume_machine", machine_id)

    def collect_results(self, machine_id: str, *, download_evidence: bool = True) -> dict:
        return self._result("collect_results", machine_id, download_evidence)

    def dispatch_task(self, body: dict) -> dict:
        return self._result("dispatch_task", body)

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
        ("/api/v1/manager/machines", ("list_machines",)),
        ("/api/v1/manager/tasks", ("list_tasks",)),
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
            "/api/v1/manager/machines/EXECUTOR-01/check",
            {},
            ("check_machine", "EXECUTOR-01"),
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
