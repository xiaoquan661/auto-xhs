"""Open the Chrome Profile bound to an account slot on Windows."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from account_manager import AccountConfig
from platforms import get_platform_spec
from project_paths import legacy_script_path


def launch_on_windows_desktop(
    command: list[str],
    *,
    user_data_dir: str,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict:
    """Ask Task Scheduler to open Chrome in the logged-in user's desktop session."""
    script = legacy_script_path("start-chrome-desktop.ps1")
    payload = {
        "executable": command[0],
        "arguments": subprocess.list2cmdline([*command[1:-1], "--new-window", command[-1]]),
        "user_data_dir": user_data_dir,
    }
    try:
        completed = runner(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
            ],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            capture_output=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("等待桌面启动响应超时，请检查员工机桌面上的 Chrome 窗口") from exc
    try:
        result = json.loads(completed.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("桌面启动服务未返回有效结果，请检查 Windows 任务计划程序") from exc
    if completed.returncode or not result.get("success"):
        raise RuntimeError(result.get("message") or "无法在员工机桌面启动 Chrome")
    return {key: value for key, value in result.items() if key != "success"}


def find_chrome_executable(
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    """Find the locally installed Google Chrome executable."""
    env = environ or os.environ
    command = which("chrome.exe") or which("chrome")
    if command:
        return Path(command)

    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        root = env.get(variable)
        if root:
            candidates.append(Path(root) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("未找到 Google Chrome，请先安装 Chrome 或确认安装路径")


def launch_chrome_profile(
    config: AccountConfig,
    *,
    executable_finder: Callable[[], Path] = find_chrome_executable,
    process_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
    start_url: str | None = None,
    platform: str = sys.platform,
    desktop_launcher: Callable[..., dict] = launch_on_windows_desktop,
) -> dict:
    """Open the exact User Data/Profile pair configured for one slot."""
    if not config.chrome_user_data_dir:
        raise RuntimeError(f"账号槽位 {config.name!r} 尚未绑定 Chrome User Data")

    user_data_dir = Path(config.chrome_user_data_dir).expanduser()
    profile_directory = config.chrome_profile_directory or "Default"
    start_url = start_url or get_platform_spec(config.platform).home_url
    if not user_data_dir.is_dir():
        raise FileNotFoundError(f"Chrome User Data 目录不存在: {user_data_dir}")
    if config.profile_mode == "existing" and not (user_data_dir / profile_directory).is_dir():
        raise FileNotFoundError(
            f"绑定的 Chrome Profile 不存在: {user_data_dir / profile_directory}"
        )

    executable = executable_finder()
    command = [
        str(executable),
        f"--user-data-dir={user_data_dir}",
        f"--profile-directory={profile_directory}",
        start_url,
    ]
    if platform == "win32":
        launch = desktop_launcher(command, user_data_dir=str(user_data_dir))
    else:
        process = process_factory(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            start_new_session=True,
        )
        launch = {"pid": getattr(process, "pid", None)}
    return {
        "launch_attempted": True,
        "launched": True,
        **launch,
        "executable": str(executable),
        "user_data_dir": str(user_data_dir),
        "profile_directory": profile_directory,
        "start_url": start_url,
        "managed_process": False,
    }
