"""Open the Chrome Profile bound to an account slot on Windows."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from account_manager import AccountConfig

DEFAULT_START_URL = "https://www.xiaohongshu.com/explore"


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
    start_url: str = DEFAULT_START_URL,
) -> dict:
    """Open the exact User Data/Profile pair configured for one slot."""
    if not config.chrome_user_data_dir:
        raise RuntimeError(f"账号槽位 {config.name!r} 尚未绑定 Chrome User Data")

    user_data_dir = Path(config.chrome_user_data_dir).expanduser()
    profile_directory = config.chrome_profile_directory or "Default"
    if not user_data_dir.is_dir():
        raise FileNotFoundError(f"Chrome User Data 目录不存在: {user_data_dir}")
    if config.profile_mode == "existing" and not (user_data_dir / profile_directory).is_dir():
        raise FileNotFoundError(
            f"绑定的 Chrome Profile 不存在: {user_data_dir / profile_directory}"
        )

    executable = executable_finder()
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = process_factory(
        [
            str(executable),
            f"--user-data-dir={user_data_dir}",
            f"--profile-directory={profile_directory}",
            start_url,
        ],
        **kwargs,
    )
    return {
        "launch_attempted": True,
        "launched": True,
        "pid": getattr(process, "pid", None),
        "executable": str(executable),
        "user_data_dir": str(user_data_dir),
        "profile_directory": profile_directory,
        "start_url": start_url,
        "managed_process": False,
    }
