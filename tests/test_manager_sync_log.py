from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from manager_sync_log import (
    acquire_viewer,
    read_log,
    release_viewer,
    start_log_window,
    write_sync_log,
)

from tests.test_manager_transport import RecordingRunner, machine, transport


def test_log_tails_new_progress_once(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "sync.log"
    write_sync_log(path, "PC057 · 回收开始")
    content, offset = read_log(path, None)
    assert "PC057 · 回收开始" in content
    assert read_log(path, offset) == ("", offset)
    write_sync_log(path, "回收完成")
    content, offset = read_log(path, offset)
    assert "回收完成" in content
    assert "回收开始" not in content
    path.write_text("新日志\n", encoding="utf-8")
    assert read_log(path, offset)[0] == "新日志\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows console singleton")
def test_viewer_reuses_owner_until_it_closes(tmp_path: Path) -> None:
    path = tmp_path / "sync.log"
    first = acquire_viewer(path)
    assert first is not None
    try:
        assert acquire_viewer(path) is None
    finally:
        release_viewer(first)
    second = acquire_viewer(path)
    assert second is not None
    release_viewer(second)


@pytest.mark.skipif(os.name != "nt", reason="Windows console launch")
def test_viewer_launch_is_hidden_until_singleton_acquired(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    start_log_window(tmp_path / "sync.log", "C:/Python/python.exe")
    args, options = calls[0]
    assert args[0][0].endswith("pythonw.exe")
    assert options["creationflags"] == subprocess.CREATE_NO_WINDOW


def test_transport_logs_steps_without_request_or_response_bodies(tmp_path: Path) -> None:
    client = transport(RecordingRunner(stdout='{"success":true,"content":"response-private"}'))
    client.activity_log = tmp_path / "sync.log"
    client.invoke(machine(), "health", {"secret": "request-private"})
    log = client.activity_log.read_text(encoding="utf-8")
    assert "192.168.1.28 · 远程请求 health：开始" in log
    assert "通信完成" in log
    assert "request-private" not in log
    assert "response-private" not in log
