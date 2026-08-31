from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.manager_transport import (
    ManagerMachineConfig,
    ManagerTransportError,
    OfflineError,
    OpenSSHTransport,
    RemoteFailedError,
    ResultUnknownError,
)


class RecordingRunner:
    def __init__(
        self,
        *,
        returncode: int = 0,
        stdout: str = '{"status":"SUCCESS"}',
        stderr: str = "",
        error: Exception | None = None,
    ) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.error = error
        self.calls: list[tuple[list[str], dict[str, Any]]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append((command, kwargs))
        if self.error:
            raise self.error
        return subprocess.CompletedProcess(
            command,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def machine() -> ManagerMachineConfig:
    return ManagerMachineConfig(
        ssh_host="192.168.1.28",
        ssh_port=2222,
        ssh_user="EDY",
        working_directory=r"C:\manager worker",
        python_executable=r"C:\Python312\python.exe",
        identity_file=r"C:\keys\manager_ed25519",
    )


def transport(runner: RecordingRunner) -> OpenSSHTransport:
    return OpenSSHTransport(
        connect_timeout=7,
        operation_timeout=25,
        runner=runner,
        environ={},
        which=lambda name: rf"C:\OpenSSH\{name}",
    )


def test_invoke_builds_ssh_command_and_sends_payload_only_through_stdin() -> None:
    runner = RecordingRunner(stdout='{"task_id":"task-1","status":"SUCCESS"}')
    client = transport(runner)
    payload = {"task_id": "task-1", "content": "仅可出现在标准输入"}

    result = client.invoke(machine(), "run_task", payload)

    command, kwargs = runner.calls[0]
    assert result == {"task_id": "task-1", "status": "SUCCESS"}
    assert command[:7] == [
        r"C:\OpenSSH\ssh.exe",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "ConnectTimeout=7",
    ]
    assert command[command.index("-p") + 1] == "2222"
    assert command[command.index("-i") + 1] == r"C:\keys\manager_ed25519"
    assert "EDY@192.168.1.28" in command
    assert command[-1] == (r'C:\Python312\python.exe "C:\manager worker\scripts\manager_worker.py"')
    assert (
        kwargs["input"]
        == json.dumps(
            {"action": "run_task", "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert payload["content"] not in " ".join(command)
    assert kwargs["timeout"] == 25
    assert kwargs["encoding"] == "utf-8"


def test_invoke_maps_ssh_exit_255_to_offline() -> None:
    client = transport(RecordingRunner(returncode=255, stderr="Connection timed out"))

    with pytest.raises(OfflineError) as captured:
        client.invoke(machine(), "run_task", {"task_id": "task-1"})

    assert captured.value.code == "OFFLINE"
    assert "执行机离线" in str(captured.value)


def test_invoke_timeout_has_unknown_result() -> None:
    timeout = subprocess.TimeoutExpired(["ssh"], timeout=25)
    client = transport(RecordingRunner(error=timeout))

    with pytest.raises(ResultUnknownError) as captured:
        client.invoke(machine(), "run_task", {"task_id": "task-1"})

    assert captured.value.code == "RESULT_UNKNOWN"
    assert captured.value.result_unknown is True
    assert "结果未知" in str(captured.value)


def test_invoke_maps_nonzero_remote_exit_to_remote_failed() -> None:
    client = transport(RecordingRunner(returncode=7, stderr="worker failed"))

    with pytest.raises(RemoteFailedError) as captured:
        client.invoke(machine(), "run_task", {"task_id": "task-1"})

    assert captured.value.code == "REMOTE_FAILED"
    assert captured.value.result_unknown is False


@pytest.mark.parametrize("stdout", ["", "not-json", "[]"])
def test_invoke_rejects_missing_or_non_object_json(stdout: str) -> None:
    client = transport(RecordingRunner(stdout=stdout))

    with pytest.raises(ResultUnknownError, match="JSON"):
        client.invoke(machine(), "run_task", {"task_id": "task-1"})


def test_upload_builds_scp_command() -> None:
    runner = RecordingRunner(stdout="")
    client = transport(runner)

    result = client.upload(
        machine(),
        r"C:\packages\task.zip",
        r"C:\manager\inbox\task.zip",
    )

    command, kwargs = runner.calls[0]
    assert command[0] == r"C:\OpenSSH\scp.exe"
    assert command[command.index("-P") + 1] == "2222"
    assert command[-2:] == [
        r"C:\packages\task.zip",
        r"EDY@192.168.1.28:C:\manager\inbox\task.zip",
    ]
    assert kwargs["input"] is None
    assert result == {
        "success": True,
        "source": r"C:\packages\task.zip",
        "remote_path": r"C:\manager\inbox\task.zip",
    }


def test_upload_adds_recursive_option_for_directory(tmp_path: Path) -> None:
    source = tmp_path / "task-package"
    source.mkdir()
    runner = RecordingRunner(stdout="")
    client = transport(runner)

    client.upload(machine(), source, r"C:\manager\inbox\task-package")

    command, _kwargs = runner.calls[0]
    assert "-r" in command
    assert command[-2:] == [
        str(source),
        r"EDY@192.168.1.28:C:\manager\inbox\task-package",
    ]


def test_download_builds_scp_command() -> None:
    runner = RecordingRunner(stdout="")
    client = transport(runner)

    result = client.download(
        machine(),
        r"C:\manager\results\result.json",
        Path(r"C:\results\result.json"),
    )

    command, _kwargs = runner.calls[0]
    assert command[0] == r"C:\OpenSSH\scp.exe"
    assert command[-2:] == [
        r"EDY@192.168.1.28:C:\manager\results\result.json",
        r"C:\results\result.json",
    ]
    assert result["destination"] == r"C:\results\result.json"


def test_discovers_ssh_from_xhs_openssh_home(tmp_path: Path) -> None:
    ssh = tmp_path / "ssh.exe"
    ssh.write_text("", encoding="utf-8")
    runner = RecordingRunner()
    client = OpenSSHTransport(
        runner=runner,
        environ={"XHS_OPENSSH_HOME": str(tmp_path)},
        which=lambda _name: None,
    )

    client.invoke(machine(), "run_task", {"task_id": "task-1"})

    assert runner.calls[0][0][0] == str(ssh)


def test_discovers_ssh_from_colleague_openssh_bundle_root(tmp_path: Path) -> None:
    bundle = tmp_path / "OpenSSH-Win64"
    bundle.mkdir()
    ssh = bundle / "ssh.exe"
    ssh.write_text("", encoding="utf-8")
    runner = RecordingRunner()
    client = OpenSSHTransport(
        runner=runner,
        environ={"XHS_OPENSSH_HOME": str(tmp_path)},
        which=lambda _name: None,
    )

    client.invoke(machine(), "health", {})

    assert runner.calls[0][0][0] == str(ssh)


def test_manager_transport_error_has_stable_service_fields() -> None:
    error = ManagerTransportError("OFFLINE", "执行机未连接")

    assert error.code == "OFFLINE"
    assert error.message == "执行机未连接"
    assert error.result_unknown is False
