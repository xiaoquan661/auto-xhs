from __future__ import annotations

from types import SimpleNamespace

from scripts.web_lifecycle import start_webui


def test_start_webui_reuses_existing_service(tmp_path) -> None:
    called = []

    result = start_webui(
        python_executable="python",
        project_root=tmp_path,
        health_checker=lambda _url: True,
        process_factory=lambda *args, **kwargs: called.append((args, kwargs)),
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
    )

    assert result["status"] == "started"
    assert result["pid"] == 24680
    assert calls[0][0][-2:] == ["--port", "8877"]
    assert (product / "webui-process.json").exists()


def test_start_webui_reports_failed_health_check(tmp_path) -> None:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    process = SimpleNamespace(pid=123)

    result = start_webui(
        python_executable="python-test",
        project_root=project,
        process_factory=lambda *_args, **_kwargs: process,
        health_checker=lambda _url: False,
    )

    assert result["success"] is False
    assert result["status"] == "start_failed"
