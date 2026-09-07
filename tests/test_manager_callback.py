from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

from manager_receiver import dispatch_request, main
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_worker_service import ManagerWorkerService
from worker_callback import OpenSSHManagerCallback


class FakeProcess:
    pid = 4321


def manager_with_task(tmp_path: Path) -> ManagerService:
    manager = ManagerService(store=ManagerStore(tmp_path / "manager"))
    manager.register_machine(
        {
            "machine_id": "PC057",
            "ssh_host": "PC057",
            "ssh_user": "EDY",
            "working_directory": r"C:\auto-xhs\xiaohongshu-skills",
            "allowed_versions": ["0.1.0"],
        }
    )
    manager.store.put(
        "tasks",
        "task-1",
        {
            "task_id": "task-1",
            "dispatch_id": "dispatch-1",
            "machine_id": "PC057",
            "remote_execution_id": "execution-1",
            "request": {
                "account_slot": "tesssst",
                "capability": "home-engagement",
                "source_type": "manager-agent",
                "schedule_id": "",
                "operation_task_id": "",
                "goal_id": "",
                "goal_snapshot": {},
                "persona_snapshot": {},
            },
            "dispatch_state": "ACCEPTED",
            "business_state": "",
            "state": "ACCEPTED",
            "reconciliation_required": False,
            "created_at": "2026-09-03T00:00:00+00:00",
            "updated_at": "2026-09-03T00:00:00+00:00",
        },
    )
    return manager


def test_manager_receiver_records_remote_agent_message_and_result(tmp_path: Path) -> None:
    manager = manager_with_task(tmp_path)
    progress = {
        "message_id": "dispatch-1:task-progress",
        "task_id": "task-1",
        "from_agent": "PC057-AGENT",
        "to_agent": "MANAGER-AGENT-01",
        "message_type": "TASK_PROGRESS",
        "status": "RUNNING",
        "summary": "员工Agent开始执行任务",
        "payload": {"remote_execution_id": "execution-1"},
    }

    message_ack = dispatch_request(
        manager,
        {
            "action": "agent-message",
            "payload": {"machine_id": "PC057", "agent_message": progress},
        },
    )
    result_ack = dispatch_request(
        manager,
        {
            "action": "task-result",
            "payload": {
                "machine_id": "PC057",
                "agent_message": {
                    **progress,
                    "message_id": "dispatch-1:task-result",
                    "message_type": "TASK_RESULT",
                    "status": "SUCCESS",
                },
                "result": {
                    "sequence": 1,
                    "result_revision": 1,
                    "task_id": "task-1",
                    "remote_execution_id": "execution-1",
                    "state": "SUCCESS",
                    "item_results": [{"feed_id": "feed-1"}],
                    "incremental_data": [],
                    "message": "任务完成",
                },
            },
        },
    )

    assert message_ack["status"] == "MESSAGE_ACK"
    assert result_ack["status"] == "RESULT_ACK"
    assert manager.store.get_collection_cursor("PC057") == 1
    assert manager.get_task("task-1")["task"]["business_state"] == "SUCCESS"
    assert {item["message_type"] for item in manager.list_agent_messages()["agent_messages"]} == {
        "TASK_PROGRESS",
        "TASK_RESULT",
        "RESULT_ACK",
    }


def test_manager_receiver_main_uses_requested_state_root(tmp_path: Path) -> None:
    manager = manager_with_task(tmp_path)
    request = {
        "action": "agent-message",
        "payload": {
            "machine_id": "PC057",
            "agent_message": {
                "message_id": "message-1",
                "task_id": "task-1",
                "from_agent": "PC057-AGENT",
                "to_agent": "MANAGER-AGENT-01",
                "message_type": "TASK_PROGRESS",
                "status": "RUNNING",
                "summary": "开始执行",
            },
        },
    }
    output = io.StringIO()
    response_file = tmp_path / "response.json"

    exit_code = main(
        state_root=manager.store.root,
        response_file=response_file,
        stdin=io.StringIO(json.dumps(request, ensure_ascii=False)),
        stdout=output,
    )

    assert exit_code == 0
    assert json.loads(output.getvalue())["status"] == "MESSAGE_ACK"
    assert json.loads(response_file.read_text(encoding="utf-8"))["status"] == "MESSAGE_ACK"


def test_worker_relays_distinct_item_events_before_execution_finishes(tmp_path: Path) -> None:
    import threading

    worker = ManagerWorkerService(tmp_path / "worker", account_summary_reader=lambda: [])
    worker.configure(machine_id="PC057", role="executor", project_version="0.1.0")
    accepted = worker.accept_task({
        "task_id": "task-live", "dispatch_id": "dispatch-live", "target_machine_id": "PC057",
        "allowed_versions": ["0.1.0"],
        "task": {"account_slot": "tesssst", "capability": "home-engagement"},
    })
    execution_id = accepted["execution"]["remote_execution_id"]
    events = [{"sequence": index, "stage": "browsing", "summary": f"浏览 {index}/2",
               "metrics": {"browse": {"actual": index, "target": 2}}} for index in (1, 2)]
    forwarded = threading.Event()

    def local_request(method, path, *args, **kwargs):
        return {"task": {"state": "RUNNING", "progress_events": events}}

    original = worker._forward_progress_events

    def forward(task, records):
        original(task, records)
        forwarded.set()

    worker._local_api_request = local_request
    worker._forward_progress_events = forward
    task = {"remote_execution_id": execution_id}
    with worker._relay_local_progress(task, "local-task", "token"):
        assert forwarded.wait(5)
        assert worker._store.snapshot()["executions"][execution_id]["state"] == "ACCEPTED"
    original(task, events)
    messages = worker._store.snapshot()["executions"][execution_id]["agent_messages"]
    live = [message for message in messages if message["payload"].get("evidence_mode") == "live"]
    assert len(live) == 2
    assert [message["payload"]["metrics"]["browse"]["actual"] for message in live] == [1, 2]


def test_worker_start_returns_ack_before_business_executor_runs(tmp_path: Path) -> None:
    started: list[tuple[list[str], dict]] = []
    executed: list[dict] = []

    def process_starter(command: list[str], **kwargs: object) -> FakeProcess:
        started.append((command, kwargs))
        return FakeProcess()

    worker = ManagerWorkerService(
        tmp_path / "worker",
        executor=lambda payload: executed.append(payload) or {"state": "SUCCESS"},
        account_summary_reader=lambda: [],
        process_starter=process_starter,
    )
    worker.configure(machine_id="PC057", role="executor", project_version="0.1.0")
    accepted = worker.accept_task(
        {
            "task_id": "task-1",
            "dispatch_id": "dispatch-1",
            "target_machine_id": "PC057",
            "allowed_versions": ["0.1.0"],
            "task": {"account_slot": "tesssst", "capability": "home-engagement"},
        }
    )

    first = worker.start_task("task-1")
    second = worker.start_task("task-1")

    assert accepted["status"] == "TASK_ACK"
    assert first["status"] == "TASK_ACK"
    assert first["runner_pid"] == 4321
    assert second["execution"]["idempotent"] is True
    assert len(started) == 1
    assert "manager_worker_async.py" in " ".join(started[0][0])
    assert executed == []


def test_worker_sends_progress_result_and_receives_result_ack(tmp_path: Path) -> None:
    callbacks: list[tuple[str, dict]] = []

    def callback_sender(_config: dict, action: str, payload: dict) -> dict:
        callbacks.append((action, payload))
        if action == "task-result":
            return {
                "success": True,
                "status": "RESULT_ACK",
                "agent_message": {
                    "message_id": "dispatch-1:result-ack",
                    "task_id": "task-1",
                    "from_agent": "MANAGER-AGENT-01",
                    "to_agent": "PC057-AGENT",
                    "message_type": "RESULT_ACK",
                    "status": "SUCCESS",
                    "summary": "经理Agent已接收员工机任务结果",
                    "payload": {},
                },
            }
        return {"success": True, "status": "MESSAGE_ACK"}

    worker = ManagerWorkerService(
        tmp_path / "worker",
        executor=lambda _payload: {
            "state": "SUCCESS",
            "message": "任务完成",
            "item_results": [{"feed_id": "feed-1"}],
        },
        account_summary_reader=lambda: [],
        callback_sender=callback_sender,
    )
    worker.configure(machine_id="PC057", role="executor", project_version="0.1.0")
    worker.accept_task(
        {
            "task_id": "task-1",
            "dispatch_id": "dispatch-1",
            "target_machine_id": "PC057",
            "allowed_versions": ["0.1.0"],
            "task": {
                "account_slot": "tesssst",
                "capability": "home-engagement",
                "manager_id": "MILA-MANAGER",
                "manager_agent_id": "MANAGER-AGENT-01",
                "manager_callback": {"ssh_host": "PC023"},
            },
        }
    )

    worker.send_task_progress("task-1")
    worker.run_task("task-1")
    result_ack = worker.push_task_result("task-1")
    collected = worker.collect_results()

    assert result_ack["status"] == "RESULT_ACK"
    assert [action for action, _payload in callbacks] == [
        "agent-message",
        "agent-message",
        "task-result",
    ]
    assert callbacks[1][1]["agent_message"]["payload"]["stage"] == "outbox_written"
    assert {item["message_type"] for item in callbacks[2][1]["result"]["agent_messages"]} == {
        "TASK_ACK",
        "TASK_PROGRESS",
        "TASK_RESULT",
    }
    execution = worker.task_status("task-1")["execution"]
    assert execution["callback_status"] == "RESULT_ACK"
    assert execution["agent_messages"][-1]["message_type"] == "RESULT_ACK"
    assert collected["results"][0]["state"] == "SUCCESS"


def test_openssh_callback_invokes_fixed_manager_receiver() -> None:
    calls: list[list[str]] = []
    transferred_requests: list[dict] = []

    def runner(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[0].endswith("scp.exe"):
            if not str(command[-2]).startswith("EDY@"):
                transferred_requests.append(
                    json.loads(Path(command[-2]).read_text(encoding="utf-8"))
                )
            else:
                Path(command[-1]).write_text(
                    '{"success":true,"status":"RESULT_ACK"}',
                    encoding="utf-8",
                )
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"success":true,"status":"RESULT_ACK"}',
            stderr="",
        )

    callback = OpenSSHManagerCallback(
        runner=runner,
        environ={},
        which=lambda name: rf"C:\OpenSSH\{name}.exe",
    )
    result = callback.send(
        {
            "ssh_host": "192.168.110.165",
            "ssh_port": 22,
            "ssh_user": "EDY",
            "working_directory": r"C:\auto-xhs\xiaohongshu-skills",
            "python_executable": r"C:\auto-xhs\xiaohongshu-skills\.venv\Scripts\python.exe",
            "state_root": r"C:\auto-xhs\manager-state",
            "inbox_path": r"C:\auto-xhs\manager-state\callback-inbox",
            "identity_file": r"C:\Users\EDY\.ssh\openclaw_device_ed25519",
        },
        "task-result",
        {"machine_id": "PC057"},
    )

    assert result["status"] == "RESULT_ACK"
    assert calls[0][0].endswith("scp.exe")
    assert "EDY@192.168.110.165" in calls[1]
    assert "manager_receiver.py" in calls[1][-1]
    assert r"C:\auto-xhs\manager-state" in calls[1][-1]
    assert "--request-file" in calls[1][-1]
    assert "--response-file" in calls[1][-1]
    assert transferred_requests[0]["action"] == "task-result"
