"""OpenSSH transport for manager-to-worker commands and file transfer."""

from __future__ import annotations

import json
import ntpath
import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xhs.errors import XHSError

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
ExecutableFinder = Callable[[str], str | None]


@dataclass(frozen=True, slots=True)
class ManagerMachineConfig:
    """SSH and runtime settings for one execution machine."""

    ssh_host: str
    ssh_port: int
    ssh_user: str
    working_directory: str
    python_executable: str
    identity_file: str | None = None
    connect_timeout_seconds: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ManagerMachineConfig:
        """Build a typed machine configuration from a persisted record."""
        identity_file = value.get("identity_file")
        return cls(
            ssh_host=str(value["ssh_host"]),
            ssh_port=int(value["ssh_port"]),
            ssh_user=str(value["ssh_user"]),
            working_directory=str(value["working_directory"]),
            python_executable=str(value["python_executable"]),
            identity_file=str(identity_file) if identity_file else None,
            connect_timeout_seconds=(
                int(value["connect_timeout_seconds"])
                if value.get("connect_timeout_seconds")
                else None
            ),
        )


class ManagerTransportError(XHSError):
    """Base error carrying a stable manager transport code."""

    def __init__(
        self,
        code: str,
        message: str,
        result_unknown: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.result_unknown = result_unknown


class OfflineError(ManagerTransportError):
    """The configured execution machine could not be reached."""

    def __init__(self, message: str) -> None:
        super().__init__("OFFLINE", message)


class ResultUnknownError(ManagerTransportError):
    """A timeout left the remote execution or transfer outcome unknown."""

    def __init__(self, message: str) -> None:
        super().__init__("RESULT_UNKNOWN", message, result_unknown=True)


class RemoteFailedError(ManagerTransportError):
    """The remote command ran but returned a failure exit status."""

    def __init__(self, message: str) -> None:
        super().__init__("REMOTE_FAILED", message)


class OpenSSHNotFoundError(ManagerTransportError):
    """The required OpenSSH executable is unavailable locally."""

    def __init__(self, message: str) -> None:
        super().__init__("OPENSSH_NOT_FOUND", message)


class OpenSSHTransport:
    """Invoke the fixed manager worker and transfer its files over OpenSSH."""

    def __init__(
        self,
        *,
        connect_timeout: int = 10,
        operation_timeout: float = 60.0,
        runner: CommandRunner = subprocess.run,
        environ: Mapping[str, str] | None = None,
        which: ExecutableFinder = shutil.which,
    ) -> None:
        self.connect_timeout = connect_timeout
        self.operation_timeout = operation_timeout
        self._runner = runner
        self._environ = os.environ if environ is None else environ
        self._which = which
        self._executables: dict[str, str] = {}
        self._machine_locks: dict[str, threading.Lock] = {}
        self._machine_locks_guard = threading.Lock()

    def invoke(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
        action: str,
        payload: Mapping[str, object],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        """Send one JSON request through stdin and return the worker JSON response."""
        config = self._machine_config(machine)
        command = [*self._ssh_command(config), self._remote_worker_command(config)]
        request = {"action": action, "payload": dict(payload)}
        input_text = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._machine_lock(config):
            result = self._run(
                command,
                input_text=input_text,
                action="远程任务",
                timeout_seconds=timeout_seconds,
            )
        output = (result.stdout or "").strip()
        try:
            response = json.loads(output)
        except json.JSONDecodeError as exc:
            raise ResultUnknownError("执行机未返回有效的 JSON 结果，任务结果未知") from exc
        if not isinstance(response, dict):
            raise ResultUnknownError("执行机返回的 JSON 结果不是对象，任务结果未知")
        return response

    def upload(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
        source: str | Path,
        remote_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Upload one local file to an execution machine with SCP."""
        config = self._machine_config(machine)
        remote = f"{self._destination(config)}:{remote_path}"
        command = self._scp_command(config)
        if Path(source).is_dir():
            command.append("-r")
        command.extend([str(source), remote])
        with self._machine_lock(config):
            self._run(command, action="上传文件", timeout_seconds=timeout_seconds)
        return {"success": True, "source": str(source), "remote_path": remote_path}

    def download(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
        remote_path: str,
        destination: str | Path,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Download an execution-machine file or evidence directory with SCP."""
        config = self._machine_config(machine)
        remote = f"{self._destination(config)}:{remote_path}"
        command = [*self._scp_command(config), "-r", remote, str(destination)]
        with self._machine_lock(config):
            self._run(command, action="下载文件", timeout_seconds=timeout_seconds)
        return {"success": True, "remote_path": remote_path, "destination": str(destination)}

    def sftp_upload_atomic(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
        source: str | Path,
        remote_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Upload one complete task file and expose it with a final SFTP rename."""

        config = self._machine_config(machine)
        source_path = Path(source)
        if not source_path.is_file():
            raise RemoteFailedError("SFTP任务包必须是已存在的文件")
        temporary_path = f"{remote_path}.uploading"
        batch = (
            f"put {self._sftp_quote(str(source_path))} "
            f"{self._sftp_quote(self._sftp_path(temporary_path))}\n"
            f"rename {self._sftp_quote(self._sftp_path(temporary_path))} "
            f"{self._sftp_quote(self._sftp_path(remote_path))}\n"
        )
        with self._machine_lock(config):
            self._run(
                self._sftp_command(config),
                input_text=batch,
                action="SFTP上传任务包",
                timeout_seconds=timeout_seconds,
            )
        return {"success": True, "source": str(source_path), "remote_path": remote_path}

    def sftp_download(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
        remote_path: str,
        destination: str | Path,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Download one employee outbox file through the SFTP data channel."""

        config = self._machine_config(machine)
        destination_path = Path(destination)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        batch = (
            f"get {self._sftp_quote(self._sftp_path(remote_path))} "
            f"{self._sftp_quote(str(destination_path))}\n"
        )
        with self._machine_lock(config):
            self._run(
                self._sftp_command(config),
                input_text=batch,
                action="SFTP回收员工机数据",
                timeout_seconds=timeout_seconds,
            )
        return {
            "success": True,
            "remote_path": remote_path,
            "destination": str(destination_path),
        }

    def sftp_download_many(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
        files: list[tuple[str, Path]],
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, object]:
        """Collect a batch of immutable outbox records in one SFTP connection."""
        if not files:
            return {"success": True, "downloaded": 0}
        config = self._machine_config(machine)
        commands = []
        for remote_path, destination in files:
            destination.parent.mkdir(parents=True, exist_ok=True)
            commands.append(
                f"get {self._sftp_quote(self._sftp_path(remote_path))} "
                f"{self._sftp_quote(str(destination))}\n"
            )
        with self._machine_lock(config):
            self._run(
                self._sftp_command(config),
                input_text="".join(commands),
                action="SFTP批量回收员工机事件",
                timeout_seconds=timeout_seconds,
            )
        return {"success": True, "downloaded": len(files)}

    def _machine_config(
        self,
        machine: ManagerMachineConfig | Mapping[str, object],
    ) -> ManagerMachineConfig:
        if isinstance(machine, ManagerMachineConfig):
            return machine
        return ManagerMachineConfig.from_mapping(machine)

    def _ssh_command(self, machine: ManagerMachineConfig) -> list[str]:
        command = [self._find_executable("ssh"), *self._common_options(machine), "-p"]
        command.extend([str(machine.ssh_port), self._destination(machine)])
        return command

    def _scp_command(self, machine: ManagerMachineConfig) -> list[str]:
        command = [self._find_executable("scp"), *self._common_options(machine), "-P"]
        command.append(str(machine.ssh_port))
        return command

    def _sftp_command(self, machine: ManagerMachineConfig) -> list[str]:
        command = [self._find_executable("sftp"), *self._common_options(machine), "-P"]
        command.extend([str(machine.ssh_port), "-b", "-", self._destination(machine)])
        return command

    @staticmethod
    def _sftp_path(value: str) -> str:
        return value.replace("\\", "/")

    @staticmethod
    def _sftp_quote(value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) * 2)}"'

    def _common_options(self, machine: ManagerMachineConfig) -> list[str]:
        options = [
            "-4",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"ConnectTimeout={machine.connect_timeout_seconds or self.connect_timeout}",
        ]
        if machine.identity_file:
            options.extend(["-i", machine.identity_file, "-o", "IdentitiesOnly=yes"])
        return options

    def _destination(self, machine: ManagerMachineConfig) -> str:
        return f"{machine.ssh_user}@{machine.ssh_host}"

    def _machine_lock(self, machine: ManagerMachineConfig) -> threading.Lock:
        key = f"{self._destination(machine)}:{machine.ssh_port}"
        with self._machine_locks_guard:
            return self._machine_locks.setdefault(key, threading.Lock())

    def export_business_data(self, machine: Mapping[str, object]) -> dict:
        """Read the employee SQLite snapshot through its fixed, read-only exporter."""
        config = self._machine_config(machine)
        script = ntpath.join(config.working_directory, "scripts", "worker_data_export.py")
        command = [*self._ssh_command(config),
                   subprocess.list2cmdline([config.python_executable, script])]
        with self._machine_lock(config):
            result = self._run(command, input_text="", action="运营数据回收", timeout_seconds=120)
        try:
            response = json.loads(result.stdout or "")
        except json.JSONDecodeError as exc:
            raise RemoteFailedError("员工运营数据导出格式无效") from exc
        return response

    def _remote_worker_command(self, machine: ManagerMachineConfig) -> str:
        worker = ntpath.join(
            machine.working_directory,
            "scripts",
            "manager_worker.py",
        )
        return subprocess.list2cmdline([machine.python_executable, worker])

    def _find_executable(self, name: str) -> str:
        cached = self._executables.get(name)
        if cached:
            return cached

        home = self._environ.get("XHS_OPENSSH_HOME")
        if home:
            home_path = Path(home).expanduser()
            for directory in (home_path, home_path / "OpenSSH-Win64"):
                for file_name in (f"{name}.exe", name):
                    candidate = directory / file_name
                    if candidate.is_file():
                        result = str(candidate)
                        self._executables[name] = result
                        return result

        for executable in (f"{name}.exe", name):
            discovered = self._which(executable)
            if discovered:
                self._executables[name] = discovered
                return discovered
        message = f"未找到 OpenSSH 的 {name} 命令，请配置 XHS_OPENSSH_HOME 或 PATH"
        raise OpenSSHNotFoundError(message)

    def _run(
        self,
        command: list[str],
        *,
        input_text: str | None = None,
        action: str,
        timeout_seconds: float | None,
    ) -> subprocess.CompletedProcess[str]:
        timeout = self.operation_timeout if timeout_seconds is None else timeout_seconds
        try:
            result = self._runner(
                command,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise ResultUnknownError(f"{action}超时，执行机结果未知") from exc
        except OSError as exc:
            raise OfflineError(f"无法连接执行机：{exc}") from exc

        stderr = (result.stderr or "").strip()
        if result.returncode == 255:
            message = "执行机离线或 SSH 连接不可用"
            if stderr:
                message = f"{message}：{stderr}"
            raise OfflineError(message)
        if result.returncode != 0:
            message = f"{action}失败，远端退出码为 {result.returncode}"
            if stderr:
                message = f"{message}：{stderr}"
            raise RemoteFailedError(message)
        return result
