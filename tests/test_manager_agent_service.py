from __future__ import annotations

from pathlib import Path

from manager_agent_service import ManagerAgentService
from manager_service import ManagerService
from manager_store import ManagerStore


class AgentTransport:
    def __init__(self) -> None:
        self.accepted: list[dict] = []
        self.returned: set[str] = set()

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
                    "state": "SUCCESS",
                    "project_version": "0.1.0",
                    "result_summary": "员工Agent执行完成",
                },
            }
        if action == "collect-results":
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


def create_operation(manager: ManagerService, metrics: dict[str, int]) -> str:
    goal = manager.save_goal(
        {
            "title": "PC057 运营目标",
            "desired_outcome": "完成浏览互动并回收结果",
            "period_start": "2026-09-03",
            "period_end": "2026-09-03",
            "targets": [{"machine_id": "PC057", "account_slot": "tesssst"}],
            "target_metrics": metrics,
            "content_topics": ["推荐视频"],
        }
    )["goal"]
    manager.activate_goal(goal["goal_id"])
    operation = manager.create_operation_task(
        {
            "operation_task_id": "OPERATION-PC057-AUTO-001",
            "goal_id": goal["goal_id"],
            "title": "PC057 自动执行",
            "instructions": "浏览 10 个视频，点赞 3 篇并评论 1 篇",
            "completion_criteria": "结果回收到经理控制台",
            "requested_by": "WebUI用户",
        }
    )["operation_task"]
    return str(operation["operation_task_id"])


def test_manager_agent_claims_plans_dispatches_collects_and_finishes(tmp_path: Path) -> None:
    manager, transport = build_manager(tmp_path)
    operation_task_id = create_operation(
        manager,
        {"browse": 10, "like": 3, "comment": 1},
    )

    agent = ManagerAgentService(manager, agent_id="MANAGER-AGENT-01")
    first_cycle = agent.run_cycle()
    assert first_cycle["status"] == "SUCCESS"
    assert len(transport.accepted) == 1
    assert transport.accepted[0]["parameters"]["video_only"] is True

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
    agent.run_cycle()
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
