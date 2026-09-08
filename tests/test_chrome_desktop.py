from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from account_manager import AccountConfig
from chrome_lifecycle import launch_chrome_profile, launch_on_windows_desktop


def test_windows_routes_to_interactive_desktop(tmp_path: Path) -> None:
    config = AccountConfig(
        name="desktop",
        bridge_port=19333,
        extension_dir=str(tmp_path / "extension"),
        chrome_user_data_dir=str(tmp_path),
    )
    calls = []

    def desktop(command: list[str], **kwargs: str) -> dict:
        calls.append((command, kwargs))
        return {"pid": 123, "session_id": 1, "launch_method": "interactive_desktop"}

    result = launch_chrome_profile(
        config,
        executable_finder=lambda: Path("chrome.exe"),
        platform="win32",
        desktop_launcher=desktop,
        process_factory=lambda *args, **kwargs: pytest.fail("Chrome must use the desktop launcher"),
    )
    assert calls[0][1]["user_data_dir"] == str(tmp_path)
    assert calls[0][0][2] == "--profile-directory=Default"
    assert result["session_id"] == 1
    assert result["launch_method"] == "interactive_desktop"


def test_desktop_launcher_passes_unicode_paths_and_requests_a_window() -> None:
    calls = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, '{"success":true,"session_id":1}')

    launch_on_windows_desktop(
        [
            r"C:\Program Files\Google\chrome.exe",
            r"--user-data-dir=C:\账号\User Data",
            "--profile-directory=Profile 8",
            "https://www.xiaohongshu.com/explore",
        ],
        user_data_dir=r"C:\账号\User Data",
        runner=runner,
    )
    payload = json.loads(calls[0][1]["input"])
    assert payload["user_data_dir"] == r"C:\账号\User Data"
    assert '"--profile-directory=Profile 8"' in payload["arguments"]
    assert "--new-window" in payload["arguments"]


@pytest.mark.parametrize("message", ["请先登录员工机桌面", "该 Chrome 用户目录被其他会话占用"])
def test_desktop_launch_errors_are_returned_without_background_retry(message: str) -> None:
    calls = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(command)
        return subprocess.CompletedProcess(
            command, 1, json.dumps({"success": False, "message": message}, ensure_ascii=False)
        )

    with pytest.raises(RuntimeError, match=message):
        launch_on_windows_desktop(
            ["chrome.exe", "https://example.com"], user_data_dir="C:/a", runner=runner
        )
    assert len(calls) == 1


def test_desktop_response_timeout_is_reported_once() -> None:
    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        raise subprocess.TimeoutExpired(command, 30)

    with pytest.raises(RuntimeError, match="等待桌面启动响应超时"):
        launch_on_windows_desktop(
            ["chrome.exe", "https://example.com"], user_data_dir="C:/a", runner=runner
        )
