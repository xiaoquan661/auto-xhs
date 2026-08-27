from __future__ import annotations

from types import SimpleNamespace

from scripts.web_lifecycle import restart_webui, schedule_webui_restart, start_webui


def test_start_webui_reuses_existing_service(tmp_path) -> None:
    called = []

    result = start_webui(
        python_executable="python",
        project_root=tmp_path,
        health_checker=lambda _url: True,
        process_factory=lambda *args, **kwargs: called.append((args, kwargs)),
        port_checker=lambda _host, _port: False,
    )

    assert result["status"] == "already_running"
    assert called == []


def test_start_webui_launches_once_and_records_pid(tmp_path, monkeypatch) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    (project / "scripts" / "web_server.py").write_text("", encoding="utf-8")
    product = tmp_path / "product"
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product))
    checks = iter([False, False, True])
    calls = []

    def factory(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(pid=24680)

    result = start_webui(
        python_executable="python-test",
        project_root=project,
        port=8877,
        process_factory=factory,
        health_checker=lambda _url: next(checks),
        port_checker=lambda _host, _port: False,
    )

    assert result["status"] == "started"
    assert result["pid"] == 24680
    assert calls[0][0][-2:] == ["--port", "8877"]
    assert (product / "webui-process.json").exists()


def test_start_webui_reports_failed_health_check(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    process = SimpleNamespace(pid=123, poll=lambda: 1)

    result = start_webui(
        python_executable="python-test",
        project_root=project,
        process_factory=lambda *_args, **_kwargs: process,
        health_checker=lambda _url: False,
        port_checker=lambda _host, _port: False,
    )

    assert result["success"] is False
    assert result["status"] == "process_exited"
    assert result["log_path"].endswith("webui-startup.log")


def test_start_webui_reports_unhealthy_port_without_spawning(tmp_path) -> None:
    called = []

    result = start_webui(
        python_executable="python-test",
        project_root=tmp_path,
        process_factory=lambda *args, **kwargs: called.append((args, kwargs)),
        health_checker=lambda _url: False,
        port_checker=lambda _host, _port: True,
    )

    assert result["success"] is False
    assert result["status"] == "port_in_use"
    assert "8765" in result["message"]
    assert called == []


def test_restart_webui_blocks_while_task_is_running(tmp_path) -> None:
    terminated = []

    result = restart_webui(
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


def test_restart_webui_replaces_idle_service(tmp_path, monkeypatch) -> None:
    product = tmp_path / "product"
    monkeypatch.setenv("XHS_PRODUCT_HOME", str(product))
    health_checks = iter([True, False, True])
    terminated = []

    result = restart_webui(
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


def test_schedule_webui_restart_launches_detached_helper(tmp_path) -> None:
    calls = []

    result = schedule_webui_restart(
        python_executable="python-test",
        project_root=tmp_path,
        port=8877,
        process_factory=lambda command, **kwargs: (
            calls.append((command, kwargs)) or SimpleNamespace(pid=789)
        ),
    )

    command = calls[0][0]
    assert result["status"] == "restart_scheduled"
    assert result["helper_pid"] == 789
    assert "restart" in command
    assert command[command.index("--port") + 1] == "8877"
