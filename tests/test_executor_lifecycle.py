from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from scripts.executor_lifecycle import restart_executor_service, start_executor_service


def test_start_executor_service_reuses_existing_service(tmp_path) -> None:
    called = []

    result = start_executor_service(
        python_executable="python",
        project_root=tmp_path,
        health_checker=lambda _url: True,
        process_factory=lambda *args, **kwargs: called.append((args, kwargs)),
        port_checker=lambda _host, _port: False,
    )

    assert result["status"] == "already_running"
    assert called == []


def test_start_executor_service_launches_api_process(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    product = tmp_path / "product"
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product))
    checks = iter([False, False, True])
    calls = []

    def factory(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=24680)

    result = start_executor_service(
        python_executable="python-test",
        project_root=project,
        port=8878,
        process_factory=factory,
        health_checker=lambda _url: next(checks),
        port_checker=lambda _host, _port: False,
        pid_reader=lambda _host, _port: 24680,
    )

    assert result["status"] == "started"
    assert result["pid"] == 24680
    assert calls[0][0][1].endswith("executor_server.py")
    assert calls[0][0][-2:] == ["--port", "8878"]
    if sys.platform == "win32":
        assert calls[0][1]["creationflags"] & subprocess.CREATE_BREAKAWAY_FROM_JOB
    assert (product / "executor-service-process.json").exists()


def test_start_executor_service_reports_unhealthy_port(tmp_path) -> None:
    called = []

    result = start_executor_service(
        python_executable="python-test",
        project_root=tmp_path,
        process_factory=lambda *args, **kwargs: called.append((args, kwargs)),
        health_checker=lambda _url: False,
        port_checker=lambda _host, _port: True,
    )

    assert result["success"] is False
    assert result["status"] == "port_in_use"
    assert "8766" in result["message"]
    assert called == []


def test_restart_executor_service_blocks_while_task_is_running(tmp_path) -> None:
    terminated = []

    result = restart_executor_service(
        python_executable="python-test",
        project_root=tmp_path,
        health_checker=lambda _url: True,
        task_reader=lambda _url: [{"task_id": "task-1", "state": "RUNNING"}],
        pid_reader=lambda _host, _port: 123,
        process_terminator=terminated.append,
    )

    assert result["status"] == "active_tasks"
    assert result["task_ids"] == ["task-1"]
    assert terminated == []


def test_restart_executor_service_replaces_idle_process(tmp_path, monkeypatch) -> None:
    product = tmp_path / "product"
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product))
    health_checks = iter([True, False, True])
    terminated = []

    result = restart_executor_service(
        python_executable="python-test",
        project_root=tmp_path,
        health_checker=lambda _url: next(health_checks),
        task_reader=lambda _url: [],
        pid_reader=lambda _host, _port: 123,
        process_terminator=terminated.append,
        port_checker=lambda _host, _port: False,
        process_factory=lambda *_args, **_kwargs: SimpleNamespace(pid=456, poll=lambda: None),
    )

    assert result["status"] == "restarted"
    assert result["previous_pid"] == 123
    assert result["pid"] == 456
    assert terminated == [123]
