from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from scripts.cli import _ensure_bridge_ready


def test_bridge_process_is_detached_from_agent(monkeypatch) -> None:
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
    config = SimpleNamespace(
        name="alpha",
        bridge_port=19701,
        account_id="slot-alpha",
        bridge_token="token-alpha",
        extension_instance_id="instance-alpha",
    )

    _ensure_bridge_ready("ws://localhost:19701", config, open_browser=False)

    command, kwargs = popen_calls[0]
    assert command[0] == sys.executable
    assert command[-4:] == ["--port", "19701", "--account", "alpha"]
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    if sys.platform == "win32":
        assert kwargs["creationflags"] & subprocess.DETACHED_PROCESS
        assert kwargs["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert kwargs["start_new_session"] is True
