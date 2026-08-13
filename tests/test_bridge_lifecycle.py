from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

import pytest

from scripts.bridge_lifecycle import get_bridge_lifecycle, start_bridge, stop_bridge
from scripts.cli import _ensure_bridge_ready


def test_bridge_process_is_detached_from_agent(monkeypatch, tmp_path) -> None:
    state = {"started": False}
    popen_calls = []

    class FakeBridgePage:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def is_server_running(self) -> bool:
            return state["started"]

    def fake_popen(command, **kwargs):
        popen_calls.append((command, kwargs))
        state["started"] = True
        return SimpleNamespace(pid=1234)

    monkeypatch.setattr("xhs.bridge.BridgePage", FakeBridgePage)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = SimpleNamespace(
        name="alpha",
        bridge_port=19701,
        account_id="slot-alpha",
        bridge_token="token-alpha",
        extension_instance_id="instance-alpha",
    )

    _ensure_bridge_ready("ws://localhost:19701", config)

    command, kwargs = popen_calls[0]
    assert command[0] == sys.executable
    assert command[-6:] == [
        "--port",
        "19701",
        "--account",
        "alpha",
        "--profile-directory",
        "Default",
    ]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    if sys.platform == "win32":
        assert kwargs["creationflags"] & subprocess.DETACHED_PROCESS
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs["start_new_session"] is True


def test_bridge_start_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = SimpleNamespace(name="alpha", bridge_port=19702, bridge_url="ws://localhost:19702")

    class RunningPage:
        def __init__(self, *_args, **_kwargs):
            pass

        def is_server_running(self):
            return True

    result = start_bridge(
        config,
        page_factory=RunningPage,
        process_factory=lambda *_args, **_kwargs: pytest.fail("must not start twice"),
    )

    assert result["bridge_running"] is True
    assert result["registered"] is False


def test_bridge_stop_requires_registered_process(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = SimpleNamespace(name="alpha", bridge_port=19703, bridge_url="ws://localhost:19703")

    with pytest.raises(RuntimeError, match="本项目登记"):
        stop_bridge(config, process_checker=lambda _pid: True)


def test_stale_bridge_pid_is_cleared(tmp_path, monkeypatch) -> None:
    from scripts.bridge_lifecycle import _write_process_record, bridge_process_path

    monkeypatch.setenv("XHS_ACCOUNTS_HOME", str(tmp_path / "accounts"))
    config = SimpleNamespace(name="alpha", bridge_port=19704, bridge_url="ws://localhost:19704")
    _write_process_record("alpha", 999999, 19704)

    class OfflinePage:
        def __init__(self, *_args, **_kwargs):
            pass

        def is_server_running(self):
            return False

    status = get_bridge_lifecycle(
        config,
        page_factory=OfflinePage,
        process_checker=lambda _pid: False,
    )

    assert status["registered"] is False
    assert not bridge_process_path("alpha").exists()
