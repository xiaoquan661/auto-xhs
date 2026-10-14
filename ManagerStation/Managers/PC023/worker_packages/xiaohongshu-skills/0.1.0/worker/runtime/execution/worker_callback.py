"""Send employee Agent messages and results back to the manager through SSH."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


class WorkerCallbackError(RuntimeError):
    """The manager callback did not return a usable acknowledgement."""


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ExecutableFinder = Callable[[str], str | None]


class OpenSSHManagerCallback:
    """Invoke the manager's fixed receiver over an employee-to-manager SSH session."""

    def __init__(
        self,
        *,
        runner: CommandRunner = subprocess.run,
        environ: Mapping[str, str] | None = None,
        which: ExecutableFinder = shutil.which,
    ) -> None:
        self._runner = runner
        self._environ = os.environ if environ is None else environ
        self._which = which

    def send(
        self,
        config: Mapping[str, Any],
        action: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        request_path = Path(tempfile.gettempdir()) / f"xhs-manager-callback-{uuid.uuid4().hex}.json"
        response_path = request_path.with_suffix(".response.json")
        inbox_path = str(
            config.get("inbox_path") or Path(str(config["state_root"])) / "callback-inbox"
        )
        remote_request_path = Path(inbox_path) / request_path.name
        remote_response_path = Path(inbox_path) / response_path.name
        receiver = str(Path(str(config["working_directory"])) / "scripts" / "manager_receiver.py")
        remote_command = subprocess.list2cmdline(
            [
                str(config.get("python_executable") or "python"),
                receiver,
                "--state-root",
                str(config["state_root"]),
                "--request-file",
                str(remote_request_path),
                "--response-file",
                str(remote_response_path),
            ]
        )
        ssh_options = [
            "-F",
            "NUL",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"ConnectTimeout={int(config.get('connect_timeout_seconds') or 10)}",
        ]
        identity_file = str(config.get("identity_file") or "").strip()
        if identity_file:
            ssh_options.extend(["-i", identity_file, "-o", "IdentitiesOnly=yes"])
        ssh_command = [
            self._find_openssh("ssh.exe", "ssh"),
            "-n",
            *ssh_options,
            "-p",
            str(int(config.get("ssh_port") or 22)),
            f"{config['ssh_user']}@{config['ssh_host']}",
            remote_command,
        ]
        scp_command = [
            self._find_openssh("scp.exe", "scp"),
            *ssh_options,
            "-P",
            str(int(config.get("ssh_port") or 22)),
            str(request_path),
            (f"{config['ssh_user']}@{config['ssh_host']}:{remote_request_path.as_posix()}"),
        ]
        response_command = [
            self._find_openssh("scp.exe", "scp"),
            *ssh_options,
            "-P",
            str(int(config.get("ssh_port") or 22)),
            (f"{config['ssh_user']}@{config['ssh_host']}:{remote_response_path.as_posix()}"),
            str(response_path),
        ]
        cleanup_command = [
            self._find_openssh("ssh.exe", "ssh"),
            "-n",
            *ssh_options,
            "-p",
            str(int(config.get("ssh_port") or 22)),
            f"{config['ssh_user']}@{config['ssh_host']}",
            subprocess.list2cmdline(["cmd", "/d", "/c", "del", "/q", str(remote_response_path)]),
        ]
        request = json.dumps(
            {"action": action, "payload": dict(payload)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        timeout_seconds = float(config.get("timeout_seconds") or 30)
        deadline = time.monotonic() + timeout_seconds
        launch_result: subprocess.CompletedProcess[str] | None = None
        try:
            request_path.write_text(request + "\n", encoding="utf-8")
            transfer = self._runner(
                scp_command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            if transfer.returncode != 0:
                raise WorkerCallbackError((transfer.stderr or "员工Agent回传文件传输失败").strip())
            try:
                launch_result = self._runner(
                    ssh_command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=min(
                        float(config.get("receiver_launch_timeout_seconds") or 1),
                        max(0.1, deadline - time.monotonic()),
                    ),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                launch_result = None
            response = self._wait_for_response(response_command, response_path, deadline)
            self._cleanup_response(cleanup_command)
        except OSError as exc:
            raise WorkerCallbackError("员工Agent连接经理机失败") from exc
        finally:
            request_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)
        if response is None:
            message = launch_result.stderr if launch_result is not None else ""
            raise WorkerCallbackError(str(message or "经理机没有返回回传确认").strip())
        if not isinstance(response, dict) or response.get("success") is not True:
            error = response.get("error") if isinstance(response, dict) else {}
            message = (
                response.get("message") or (error.get("message") if isinstance(error, dict) else "")
                if isinstance(response, dict)
                else ""
            )
            raise WorkerCallbackError(str(message or "经理机拒绝了员工Agent回传"))
        return response

    def _wait_for_response(
        self,
        command: list[str],
        response_path: Path,
        deadline: float,
    ) -> dict[str, Any] | None:
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            try:
                result = self._runner(
                    command,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=min(5.0, remaining),
                    check=False,
                )
            except subprocess.TimeoutExpired:
                result = None
            if result is not None and result.returncode == 0 and response_path.is_file():
                try:
                    response = json.loads(response_path.read_text(encoding="utf-8-sig"))
                except json.JSONDecodeError as exc:
                    raise WorkerCallbackError("经理机没有返回有效的回传确认") from exc
                return response if isinstance(response, dict) else None
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
        return None

    def _cleanup_response(self, command: list[str]) -> None:
        try:
            self._runner(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=0.5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    def _find_openssh(self, executable_name: str, path_name: str) -> str:
        configured_home = str(self._environ.get("XHS_OPENSSH_HOME") or "").strip()
        if configured_home:
            candidate = Path(configured_home) / executable_name
            if candidate.is_file():
                return str(candidate)
        for candidate in (
            Path(r"C:\Program Files\OpenClawSSH-OpenSSH") / executable_name,
            Path(r"C:\Windows\System32\OpenSSH") / executable_name,
        ):
            if candidate.is_file():
                return str(candidate)
        executable = self._which(path_name)
        if executable:
            return executable
        raise WorkerCallbackError(f"员工机没有找到 OpenSSH {path_name} 客户端")
