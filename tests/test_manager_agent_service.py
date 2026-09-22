from __future__ import annotations

from pathlib import Path

import pytest
from manager_agent_service import ManagerAgentService
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_sync_service import ManagerSyncService


class AgentTransport:
    def __init__(self) -> None:
        self.accepted: list[dict] = []
        self.returned: set[str] = set()
        self.collection_calls = 0

    def invoke(
        self,
        _machine: dict,
        action: str,
        payload: dict,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        if action == "health":
            return {
                "success": True,
                "health": {
                    "machine_id": "PC057",
                    "role": "executor",
                    "project_version": "0.1.0",
                    "healthy": True,
                    "paused": False,
                    "account_slots": [],
                },
            }
        if action == "accept-task":
            self.accepted.append(dict(payload))
            execution_id = f"execution-{len(self.accepted)}"
            return {
                "success": True,
                "status": "TASK_ACK",
                "execution": {
                    "remote_execution_id": execution_id,
                    "state": "ACCEPTED",
                },
                "agent_messages": [
                    {
                        "message_id": f"{payload['dispatch_id']}:task-ack",
                        "operation_task_id": payload["operation_task_id"],
                        "task_id": payload["task_id"],
                        "from_agent": "PC057-AGENT",
                        "to_agent": payload["manager_agent_id"],
                        "message_type": "TASK_ACK",
                        "status": "ACCEPTED",
                        "summary": "员工Agent已接收任务",
                        "payload": {"remote_execution_id": execution_id},
                    }
                ],
            }
        if action == "start-task":
            return {
                "success": True,
                "status": "TASK_ACK",
                "execution": {
                    "remote_execution_id": payload["remote_execution_id"],
                    "state": "ACCEPTED",
                    "project_version": "0.1.0",
                    "result_summary": "员工Agent执行完成",
                },
            }
        if action == "collect-results":
            self.collection_calls += 1
            results = []
            for sequence, request in enumerate(self.accepted, start=1):
                task_id = str(request["task_id"])
                if task_id in self.returned:
                    continue
                self.returned.add(task_id)
                if request["capability"] == "home-engagement":
                    item_results = [
                        {
                            "browse": {"status": "success"},
                            "like": {"status": "success" if index < 3 else "skipped"},
                        }
                        for index in range(10)
                    ]
                    results.append(
                        {
                            "sequence": sequence,
                            "result_revision": 1,
                            "task_id": task_id,
                            "remote_execution_id": f"execution-{sequence}",
                            "state": "SUCCESS",
                            "actual_count": 13,
                            "item_results": item_results,
                        }
                    )
                else:
                    results.append(
                        {
                            "sequence": sequence,
                            "result_revision": 1,
                            "task_id": task_id,
                            "remote_execution_id": f"execution-{sequence}",
                            "state": "SUCCESS",
                            "actual_count": 1,
                            "item_results": [],
                        }
                    )
            return {
                "success": True,
                "results": results,
                "next_sequence": len(self.accepted),
            }
        raise AssertionError(f"unexpected action: {action}, timeout={timeout_seconds}")


def build_manager(tmp_path: Path) -> tuple[ManagerService, AgentTransport]:
    transport = AgentTransport()
    manager = ManagerService(
        store=ManagerStore(tmp_path / "manager"),
        transport=transport,  # type: ignore[arg-type]
    )
    manager.register_machine(
        {
            "manager_id": "MILA-MANAGER",
            "machine_id": "PC057",
            "ssh_host": "192.168.110.57",
            "ssh_port": 22,
            "ssh_user": "EDY",
            "working_directory": r"C:\xiaohongshu-skills",
            "python_executable": r"C:\xiaohongshu-skills\.venv\Scripts\python.exe",
            "allowed_versions": ["0.1.0"],
        }
    )
    assert manager.check_machine("PC057")["status"] == "READY"
    manager.save_persona(
        {
            "machine_id": "PC057",
            "account_slot": "tesssst",
            "display_name": "自然浏览者",
            "role": "根据真实内容参与讨论",
            "audience": "生活方式用户",
            "content_topics": ["生活方式"],
            "tone": "自然简短",
            "interaction_style": "回应真实细节",
            "fact_boundaries": "不虚构体验",
            "knowledge": "识别标题和正文信息",
        }
    )
    return manager, transport


def test_daily_engagement_plan_budgets_employee_timing(tmp_path: Path) -> None:
    manager, _transport = build_manager(tmp_path)
    agent = ManagerAgentService(manager)
    task = {
        "operation_task_id": "daily-rhythm",
        "goal_snapshot": {"target_metrics": {"browse": 40, "like": 8}},
        "targets": [{"machine_id": "PC057", "account_slot": "tesssst"}],
    }
    action = agent._build_plan(task)["planned_actions"][0]
    parameters = action["parameters"]
    assert (parameters["min_read_seconds"], parameters["max_read_seconds"]) == (15, 45)
    assert parameters["duration_minutes"] * 60 >= 40 * 45 + 7 * 180
    request = agent._task_request(task, action)
    assert request["timeout_seconds"] > parameters["duration_minutes"] * 60


def create_operation(
    manager: ManagerService,
    metrics: dict[str, int],
    *,
    account_slot: str = "tesssst",
    operation_task_id: str = "OPERATION-PC057-AUTO-001",
) -> str:
    goal = manager.save_goal(
        {
            "title": "PC057 运营目标",
            "desired_outcome": "完成浏览互动并回收结果",
            "period_start": "2026-09-03",
            "period_end": "2026-09-03",
            "targets": [{"machine_id": "PC057", "account_slot": account_slot}],
            "target_metrics": metrics,
            "content_topics": ["推荐视频"],
        }
    )["goal"]
    manager.activate_goal(goal["goal_id"])
    operation = manager.create_operation_task(
        {
            "operation_task_id": operation_task_id,
            "goal_id": goal["goal_id"],
            "title": "PC057 自动执行",
            "instructions": "浏览 10 个视频，点赞 3 篇并评论 1 篇",
            "completion_criteria": "结果回收到经理控制台",
            "requested_by": "WebUI用户",
        }
    )["operation_task"]
    return str(operation["operation_task_id"])


@pytest.mark.parametrize("persona_identity", [{}, None])
def test_manager_agent_claims_plans_dispatches_collects_and_finishes(
    tmp_path: Path, persona_identity: dict | None
) -> None:
    manager, transport = build_manager(tmp_path)
    for persona in manager.store.list("personas"):
        manager.store.update(
            "personas", persona["persona_id"],
            lambda row: {**row, "identity": persona_identity},
        )
    operation_task_id = create_operation(
        manager,
        {"browse": 10, "like": 3, "comment": 1},
    )

    agent = ManagerAgentService(manager, agent_id="MANAGER-AGENT-01")
    first_cycle = agent.run_cycle()
    assert first_cycle["status"] == "SUCCESS"
    assert len(transport.accepted) == 1
    assert transport.accepted[0]["parameters"]["video_only"] is True
    assert transport.collection_calls == 0

    sync = ManagerSyncService(manager)
    sync.run_result_cycle()
    agent.run_cycle()
    sync.run_result_cycle()
    result = agent.run_cycle()
    operation = manager.get_operation_task(operation_task_id)["operation_task"]
    messages = manager.list_agent_messages(operation_task_id)["agent_messages"]

    assert result["status"] == "SUCCESS"
    assert operation["state"] == "COMPLETED"
    assert operation["progress"]["actual_metrics"] == {
        "browse": 10,
        "comment": 1,
        "like": 3,
    }
    assert len(transport.accepted) == 2
    assert transport.collection_calls == 2
    assert transport.accepted[1]["parameters"]["direct_send_authorized"] is True
    assert {message["message_type"] for message in messages} >= {
        "TASK_AVAILABLE",
        "TASK_CLAIMED",
        "PLAN_READY",
        "TASK_DISPATCH",
        "TASK_ACK",
        "TASK_FINISHED",
    }
    assert manager.list_sync_status()["sync"]["agent"]["status"] == "SUCCESS"


def test_manager_agent_blocks_metrics_without_an_execution_adapter(tmp_path: Path) -> None:
    manager, transport = build_manager(tmp_path)
    operation_task_id = create_operation(manager, {"publish": 1})

    result = ManagerAgentService(manager).run_cycle()
    operation = manager.get_operation_task(operation_task_id)["operation_task"]

    assert result["status"] == "FAILED"
    assert operation["state"] == "BLOCKED"
    assert "publish" in operation["result_summary"]
    assert transport.accepted == []


def test_completed_results_can_reconcile_a_previously_blocked_operation(tmp_path: Path) -> None:
    manager, _transport = build_manager(tmp_path)
    operation_task_id = create_operation(manager, {"browse": 10, "like": 3, "comment": 1})
    agent = ManagerAgentService(manager, agent_id="MANAGER-AGENT-01")
    sync = ManagerSyncService(manager)
    agent.run_cycle()
    sync.run_result_cycle()
    agent.run_cycle()
    sync.run_result_cycle()
    agent.run_cycle()
    manager.store.update(
        "operation_tasks",
        operation_task_id,
        lambda task: {
            **task,
            "state": "BLOCKED",
            "last_result_state": "RESULT_UNKNOWN",
            "result_summary": "等待结果对账",
        },
    )

    reconciled = manager.finish_operation_task(
        operation_task_id,
        {
            "manager_agent_id": "MANAGER-AGENT-01",
            "state": "COMPLETED",
            "result_summary": "员工机成功结果已回收并完成对账",
        },
    )

    assert reconciled["operation_task"]["state"] == "COMPLETED"
    assert reconciled["operation_task"]["last_result_state"] == "SUCCESS"
    assert reconciled["operation_task"]["progress"]["result_count"] == 2
    assert reconciled["operation_task"]["progress"]["actual_metrics"] == {
        "browse": 10,
        "comment": 1,
        "like": 3,
    }


def four_operations(manager: ManagerService) -> list[str]:
    identifiers = []
    for index in range(4):
        slot = f"account-{index}"
        manager.save_persona(
            {
                "machine_id": "PC057",
                "account_slot": slot,
                "display_name": slot,
                "content_topics": ["生活"],
            }
        )
        identifiers.append(
            create_operation(
                manager,
                {"browse": 10},
                account_slot=slot,
                operation_task_id=f"BATCH-{index}",
            )
        )
    return identifiers


@pytest.mark.parametrize("stage", ["plan", "request", "dispatch"])
def test_four_accounts_continue_and_resume_after_one_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    from service_errors import ServiceError

    manager, transport = build_manager(tmp_path)
    identifiers = four_operations(manager)
    agent = ManagerAgentService(manager)
    owner, name = (
        (manager, "dispatch_task")
        if stage == "dispatch"
        else (agent, "_build_plan" if stage == "plan" else "_task_request")
    )
    original = getattr(owner, name)

    def fail_first(task: dict, *args: object) -> dict:
        if task.get("operation_task_id") == identifiers[0]:
            raise ServiceError("ACCOUNT_BUSY", "账号暂时占用")
        return original(task, *args)

    monkeypatch.setattr(owner, name, fail_first)
    first = agent.run_cycle()
    assert first["processed"] == 4
    assert first["status"] == "PARTIAL_SUCCESS"
    assert {r["account_slot"] for r in transport.accepted} == {
        "account-1",
        "account-2",
        "account-3",
    }
    failed = manager.get_operation_task(identifiers[0])["operation_task"]
    assert failed["state"] == "PLANNING"
    error = failed.get("last_agent_error") or failed["plan"]["dispatch_failures"][0]
    assert "账号暂时占用" in error["message"]
    monkeypatch.setattr(owner, name, original)
    # Restart the driver: progress and retry inputs must be durable.
    second = ManagerAgentService(manager).run_cycle()
    assert second["success"]
    assert len(transport.accepted) == 4
    current = manager.get_operation_task(identifiers[0])["operation_task"]
    assert not current.get("last_agent_error")
    assert not current["plan"]["dispatch_failures"]


def test_offline_dispatch_resumes_same_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from manager_transport import ManagerTransportError

    manager, transport = build_manager(tmp_path)
    create_operation(manager, {"browse": 10})
    original = transport.invoke

    def offline(machine: dict, action: str, payload: dict, **kwargs: object) -> dict:
        if action == "accept-task":
            raise ManagerTransportError("OFFLINE", "暂时离线", result_unknown=False)
        return original(machine, action, payload, **kwargs)

    monkeypatch.setattr(transport, "invoke", offline)
    assert not ManagerAgentService(manager).run_cycle()["success"]
    pending = manager.list_tasks()["tasks"][0]
    assert pending["dispatch_state"] == "PENDING_DISPATCH"
    monkeypatch.setattr(transport, "invoke", original)
    assert ManagerAgentService(manager).run_cycle()["success"]
    assert len(transport.accepted) == 1
    resumed = manager.list_tasks()["tasks"][0]
    assert resumed["dispatch_id"] == pending["dispatch_id"]
    assert resumed["request"] == pending["request"]


def test_unknown_delivery_waits_without_resend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from manager_transport import ManagerTransportError

    manager, transport = build_manager(tmp_path)
    create_operation(manager, {"browse": 10, "comment": 1})
    original = transport.invoke
    attempts = []

    def interrupted(machine: dict, action: str, payload: dict, **kwargs: object) -> dict:
        if action == "accept-task":
            attempts.append(payload)
            raise ManagerTransportError("TIMEOUT", "确认中断", result_unknown=True)
        return original(machine, action, payload, **kwargs)

    monkeypatch.setattr(transport, "invoke", interrupted)
    assert not ManagerAgentService(manager).run_cycle()["success"]
    monkeypatch.setattr(transport, "invoke", original)
    second = ManagerAgentService(manager).run_cycle()
    assert second["outcomes"][0]["status"] == "RECONCILIATION_REQUIRED"
    assert len(attempts) == 1
    assert transport.accepted == []
    assert len(manager.list_tasks()["tasks"]) == 1


def test_failure_record_preserves_running_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from service_errors import ServiceError

    manager, transport = build_manager(tmp_path)
    identifiers = four_operations(manager)
    # Combine the four accounts in one operation to exercise its action loop.
    rows = [manager.store.get("operation_tasks", identifier) for identifier in identifiers]
    task = rows[0]
    task["targets"] = [row["targets"][0] for row in rows]
    task["persona_bindings"] = [row["persona_bindings"][0] for row in rows]
    task["goal_snapshot"]["target_metrics"] = {"browse": 40}
    manager.store.put("operation_tasks", identifiers[0], task)
    original = manager.dispatch_task

    def dispatch(request: dict) -> dict:
        if request["account_slot"] == "account-0":
            raise ServiceError("ACCOUNT_BUSY", "账号暂时占用")
        return original(request)

    monkeypatch.setattr(manager, "dispatch_task", dispatch)
    agent = ManagerAgentService(manager)
    outcome = agent._process_operation(task)
    assert not outcome["success"]
    assert len(transport.accepted) == 3
    current = manager.get_operation_task(identifiers[0])["operation_task"]
    assert current["state"] in {"DISPATCHING", "RUNNING"}
    assert len(current["plan"]["dispatch_failures"]) == 1
    monkeypatch.setattr(manager, "dispatch_task", original)
    assert agent._process_operation(current)["success"]
    assert len(transport.accepted) == 4


def test_exception_after_acceptance_does_not_duplicate_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, transport = build_manager(tmp_path)
    identifier = create_operation(manager, {"browse": 10})
    original = manager.dispatch_task

    def accepted_then_error(request: dict) -> dict:
        original(request)
        raise RuntimeError("接收后本地记录中断")

    monkeypatch.setattr(manager, "dispatch_task", accepted_then_error)
    assert not ManagerAgentService(manager).run_cycle()["success"]
    monkeypatch.setattr(manager, "dispatch_task", original)
    assert ManagerAgentService(manager).run_cycle()["success"]
    assert len(transport.accepted) == 1
    assert manager.get_operation_task(identifier)["operation_task"]["state"] != "BLOCKED"


def test_terminal_record_and_error_do_not_count_as_two_actions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, transport = build_manager(tmp_path)
    identifier = create_operation(manager, {"browse": 10, "comment": 1})
    original = manager.dispatch_task

    def recorded_then_error(request: dict) -> dict:
        original(request)
        manager.store.update(
            "tasks",
            request["task_id"],
            lambda row: {
                **row,
                "dispatch_state": "BLOCKED",
                "business_state": "BLOCKED",
                "message": "本次执行失败",
            },
        )
        raise RuntimeError("本地后处理失败")

    monkeypatch.setattr(manager, "dispatch_task", recorded_then_error)
    ManagerAgentService(manager).run_cycle()
    current = manager.get_operation_task(identifier)["operation_task"]
    assert current["state"] not in {"BLOCKED", "COMPLETED"}
    assert len(current["plan"]["planned_actions"]) == 2
    assert len(manager.list_tasks()["tasks"]) == 1
    monkeypatch.setattr(manager, "dispatch_task", original)
    ManagerAgentService(manager).run_cycle()
    assert [r["capability"] for r in transport.accepted] == ["home-engagement", "post-comment"]


@pytest.mark.parametrize("dispatch_state", ["PENDING_DISPATCH", "TRANSFERRED"])
def test_pending_delivery_reuses_saved_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dispatch_state: str,
) -> None:
    manager, transport = build_manager(tmp_path)
    create_operation(manager, {"browse": 10})
    ManagerAgentService(manager).run_cycle()
    saved = manager.list_tasks()["tasks"][0]
    manager.store.update(
        "tasks",
        saved["task_id"],
        lambda row: {
            **row,
            "dispatch_state": dispatch_state,
            "business_state": "",
        },
    )
    calls = []

    def resume(request: dict) -> dict:
        calls.append(request)
        execution = manager.store.update(
            "tasks",
            saved["task_id"],
            lambda row: {
                **row,
                "dispatch_state": "ACCEPTED",
            },
        )
        return {"success": True, "status": "TASK_ACK", "task": execution}

    monkeypatch.setattr(manager, "dispatch_task", resume)
    assert ManagerAgentService(manager).run_cycle()["success"]
    assert calls == [saved["request"]]
    assert len(transport.accepted) == 1


def test_four_claimed_empty_plans_dispatch_in_one_cycle(tmp_path: Path) -> None:
    manager, transport = build_manager(tmp_path)
    identifiers = four_operations(manager)
    agent = ManagerAgentService(manager)
    for identifier in identifiers:
        claimed = manager.claim_operation_task(identifier, {"manager_agent_id": agent.agent_id})
        assert not (claimed["operation_task"].get("plan") or {}).get("planned_actions")
    assert agent.run_cycle()["success"]
    assert {r["account_slot"] for r in transport.accepted} == {
        "account-0",
        "account-1",
        "account-2",
        "account-3",
    }
    for identifier in identifiers:
        current = manager.get_operation_task(identifier)["operation_task"]
        assert current["plan"]["planned_actions"]
        assert current["progress"]["execution_task_count"] == 1


def test_immediate_unknown_result_holds_following_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager, transport = build_manager(tmp_path)
    identifier = create_operation(manager, {"browse": 10, "comment": 1})
    original = manager.dispatch_task

    def uncertain(request: dict) -> dict:
        original(request)
        execution = manager.store.update(
            "tasks",
            request["task_id"],
            lambda row: {
                **row,
                "business_state": "RESULT_UNKNOWN",
                "reconciliation_required": True,
            },
        )
        return {"success": True, "status": "RESULT_UNKNOWN", "task": execution}

    monkeypatch.setattr(manager, "dispatch_task", uncertain)
    result = ManagerAgentService(manager).run_cycle()
    assert result["outcomes"][0]["status"] == "RECONCILIATION_REQUIRED"
    assert len(transport.accepted) == 1
    assert manager.get_operation_task(identifier)["operation_task"]["state"] != "COMPLETED"
