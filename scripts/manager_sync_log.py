"""Persistent manager synchronization log and its single Windows console viewer."""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

_WRITE_LOCK = threading.Lock()


def write_sync_log(path: Path | None, message: str) -> None:
    """Record operational summaries; request bodies remain in their existing stores."""
    if path is None:
        return
    try:
        with _WRITE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n")
    except OSError:
        # Logging availability does not change the transport result.
        return


def start_log_window(path: Path, python_executable: str) -> subprocess.Popen | None:
    """Launch an invisible viewer process; only the single owner opens a console."""
    if os.name != "nt":
        return None
    pythonw = Path(python_executable).with_name("pythonw.exe")
    return subprocess.Popen(
        [str(pythonw), str(Path(__file__).resolve()), "--log-file", str(path.resolve())],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def acquire_viewer(path: Path) -> int | None:
    """Hold a session-local mutex before opening any visible window."""
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    name = "Local\\AutoXhsSyncLog-" + str(path.resolve()).lower().replace("\\", "_")
    handle = kernel.CreateMutexW(None, False, name)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        kernel.CloseHandle(handle)
        return None
    return handle


def release_viewer(handle: int) -> None:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle(handle)


def read_log(path: Path, offset: int | None) -> tuple[str, int]:
    """Read recent history once, then consume appended lines."""
    if not path.exists():
        return "", offset or 0
    with path.open("rb") as stream:
        size = stream.seek(0, os.SEEK_END)
        start = max(0, size - 32768) if offset is None else (offset if offset <= size else 0)
        stream.seek(start)
        if offset is None and start:
            stream.readline()
        content = stream.read()
        return content.decode("utf-8", errors="replace").replace("\r\n", "\n"), stream.tell()


def show_log(path: Path) -> None:
    from ctypes import wintypes

    handle = acquire_viewer(path)
    if handle is None:
        return
    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
        if not kernel.AllocConsole():
            raise ctypes.WinError(ctypes.get_last_error())
        kernel.SetConsoleTitleW("auto-xhs 经理同步日志")
        with open("CONOUT$", "w", encoding="utf-8", buffering=1) as output:
            output.write("auto-xhs 经理同步日志\n关闭此窗口不影响后台同步。\n\n")
            offset = None
            while True:
                content, offset = read_log(path, offset)
                if content:
                    output.write(content)
                time.sleep(0.5)
    finally:
        release_viewer(handle)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="查看经理同步日志")
    parser.add_argument("--log-file", type=Path, required=True)
    show_log(parser.parse_args().log_file)
