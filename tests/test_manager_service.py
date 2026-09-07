from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_transport import ManagerTransportError
from service_errors import ServiceError


class FakeTransport:
    def __init__(self) -> None:
        self.responses: dict[str, list[dict | Exception]] = defaultdict(list)
        self.calls: list[tuple[str, dict]] = []
        self.uploads: list[tuple[str, str]] = []
        self.downloads: list[tuple[str, str]] = []
        self.download_responses: list[Exception | None] = []

    def queue(self, action: str, *responses: dict | Exception) -> None:
        self.responses[action].extend(responses)

    def queue_download(self, *responses: Exception | None) -> None:
        self.download_responses.extend(responses)

    def invoke(
        self,
        _machine: dict,
        action: str,
        payload: dict,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.calls.append((action, payload))
        queued = self.responses[action]
        if queued:
            response = queued.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        if action == "health":
            return {
                "success": True,
                "health": {
                    "machine_id": "PC001",
                    "role": "executor",
                    "project_version": "0.1.0",
                    "healthy": True,
                    "paused": False,
                    "account_slots": [],
                },
            }
        if action == "accept-task":
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "ACCEPTED",
                },
            }
        if action == "start-task":
            return {
                "success": True,
                "status": "TASK_ACK",
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "SUCCESS",
                    "project_version": "0.1.0",
                    "result_summary": "页面回读成功",
                },
            }
        if action in {"pause-task", "resume-task"}:
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "PAUSED" if action == "pause-task" else "ACCEPTED",
                },
            }
        if action in {"pause-machine", "resume-machine"}:
            return {"success": True, "machine": {"paused": action == "pause-machine"}}
        if action in {"executor-status", "webui-status"}:
            return {
                "success": True,
                "status": "RUNNING",
                "webui": {"ready": True},
                "executor_service": {"ready": True},
                "health": {
                    "machine_id": "PC001",
                    "role": "executor",
                    "project_version": "0.1.0",
                    "healthy": True,
                    "paused": False,
                    "executor_service": {"ready": True},
                    "local_service": {"ready": True},
                },
            }
        if action in {"start-executor", "restart-executor", "start-webui", "restart-webui"}:
            return {
                "success": True,
                "status": "READY",
                "message": "执行机执行服务已就绪",
                "operation": {"success": True, "status": action},
                "executor_service": {"ready": True},
                "health": {
                    "machine_id": "PC001",
                    "role": "executor",
                    "project_version": "0.1.0",
                    "healthy": True,
                    "paused": False,
                    "executor_service": {"ready": True},
                    "local_service": {"ready": True},
                },
            }
        if action == "prepare-transfer":
            return {
                "success": True,
                "transfer": {
                    "transfer_id": payload["transfer_id"],
                    "state": "PREPARED",
                    "staging_path": r"C:\manager-worker\staging\transfer-1",
                },
            }
        if action == "finalize-transfer":
            return {
                "success": True,
                "transfer": {
                    "state": "SUCCESS",
                    "actual_version": "1.0",
                    "installed_path": r"C:\manager-worker\packages\EXPLORE-01\1.0",
                },
            }
        if action == "list-function-versions":
            return {"success": True, "functions": [], "versions": []}
        if action == "set-function-version-policy":
            return {
                "success": True,
                "version": {
                    "function_id": payload["function_id"],
                    "version": payload["version"],
                    "status": payload["status"],
                    "scope": payload.get("scope") or {},
                    "stable": bool(payload.get("set_as_stable")),
                },
            }
        if action == "collect-results":
            return {"success": True, "results": [], "next_sequence": 0}
        if action == "account-management":
            return {"success": True, "management": dict(payload)}
        if action == "task-status":
            return {
                "success": True,
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "RUNNING",
                },
            }
        raise AssertionError(f"unexpected action: {action}, timeout={timeout_seconds}")

    def upload(
        self,
        _machine: dict,
        source: str,
        remote_path: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.uploads.append((source, remote_path))
        return {"success": True, "timeout_seconds": timeout_seconds}

    def download(
        self,
        _machine: dict,
        remote_path: str,
        destination: str,
        *,
        timeout_seconds: float | None = None,
    ) -> dict:
        self.downloads.append((remote_path, destination))
        if self.download_responses:
            response = self.download_responses.pop(0)
            if response is not None:
                raise response
        Path(destination).write_text("evidence", encoding="utf-8")
        return {"success": True, "timeout_seconds": timeout_seconds}


def machine_values(machine_id: str = "PC001", host: str = "192.168.1.21") -> dict:
    return {
        "manager_id": "MILA-MANAGER",
        "machine_id": machine_id,
        "ssh_host": host,
        "ssh_port": 22,
        "ssh_user": "EDY",
        "working_directory": r"C:\xiaohongshu-skills",
        "python_executable": r"C:\xiaohongshu-skills\.venv\Scripts\python.exe",
        "allowed_versions": ["0.1.0"],
    }


def task_request() -> dict:
    return {
        "task_id": "task-1",
        "machine_id": "PC001",
        "account_slot": "brand-a",
        "capability": "search-feeds",
        "request_summary": "搜索指定关键词",
        "parameters": {"keyword": "英语口语"},
        "authorization": {},
        "write_intent": False,
    }


def service(
    tmp_path: Path, transport: FakeTransport | None = None
) -> tuple[ManagerService, FakeTransport]:
    fake = transport or FakeTransport()
    return (
        ManagerService(store=ManagerStore(tmp_path / "manager"), transport=fake),  # type: ignore[arg-type]
        fake,
    )


def register_ready(manager: ManagerService) -> None:
    manager.register_machine(machine_values())
    checked = manager.check_machine("PC001")
    assert checked["status"] == "READY"


def schedule_request() -> dict:
    return {
        "schedule_id": "schedule-1",
        "machine_id": "PC001",
        "account_slot": "brand-a",
        "capability": "search-feeds",
        "request_summary": "每天采集关键词",
        "rule": {"frequency": "daily"},
        "time_window": {"start": "09:00", "end": "18:00", "timezone": "Asia/Shanghai"},
        "quota": {"per_run": 10, "daily": 30},
        "parameters": {"keyword": "英语口语"},
        "next_run_at": "2026-09-04T01:00:00Z",
    }


def persona_request() -> dict:
    return {
        "machine_id": "PC001",
        "account_slot": "brand-a",
        "display_name": "城市生活方式编辑",
        "role": "分享真实可验证的城市体验",
        "audience": "关注周末生活的年轻用户",
        "content_topics": ["城市漫游", "周末活动"],
        "tone": "自然、克制、有细节",
        "interaction_style": "结合笔记和评论上下文回复",
        "fact_boundaries": "不虚构到店体验",
        "knowledge": "熟悉城市公共空间和活动信息",
    }


def goal_request() -> dict:
    return {
        "title": "九月账号稳定增长",
        "desired_outcome": "形成稳定的城市周末内容节奏",
        "period_start": "2026-09-01",
        "period_end": "2026-09-30",
        "targets": [{"machine_id": "PC001", "account_slot": "brand-a"}],
        "target_metrics": {"browse": 20, "comment": 8, "publish": 4},
        "content_topics": ["城市漫游", "周末活动"],
    }


def operation_task_request(goal_id: str) -> dict:
    return {
        "operation_task_id": "operation-september-001",
        "goal_id": goal_id,
        "title": "执行九月城市周末运营",
        "instructions": "经理Agent拆解每日采集、互动和发布节奏",
        "completion_criteria": "完成目标指标并回收员工机记录",
        "requested_by": "WebUI用户",
    }


def remote_account(
    name: str = "brand-a",
    *,
    status: str = "READY",
    bridge: bool = True,
    extension: bool = True,
) -> dict:
    return {
        "name": name,
        "platform": "xiaohongshu",
        "profile_mode": "existing",
        "paired": True,
        "status": status,
        "ready": status == "READY",
        "server_running": bridge,
        "extension_connected": extension,
        "profile_verified": True,
        "connection_ready": bridge and extension,
        "identity": {
            "user_id": "uid-1",
            "live_user_id": "uid-1",
            "nickname": "品牌账号",
            "matches_record": True,
        },
        "next_action": "完成扩展连接" if not extension else "",
    }


def test_remote_accounts_are_keyed_cached_normalized_and_alerted(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "list-accounts",
        {
            "success": True,
            "checked_at": "2026-09-01T00:00:00+00:00",
            "accounts": [
                remote_account(),
                remote_account("brand-b", status="IDENTITY_CHECK_REQUIRED", extension=False),
            ],
        },
    )

    refreshed = manager.list_machine_accounts("pc001")
    cached = manager.list_accounts("PC001")
    alerts = manager.list_account_alerts("PC001")

    assert refreshed["success"] is True
    assert [item["account_key"] for item in cached["accounts"]] == [
        "PC001:brand-a",
        "PC001:brand-b",
    ]
    assert cached["summary"] == {
        "total": 2,
        "BLOCKED": 0,
        "READY": 1,
        "UNKNOWN": 0,
        "WARNING": 1,
        "RUNNING": 0,
    }
    assert cached["accounts"][0]["uid"] == "uid-1"
    assert alerts["total"] == 1
    assert alerts["alerts"][0]["severity"] == "WARNING"
    assert transport.calls[-1] == ("list-accounts", {})
    machine = manager.list_machines()["machines"][0]
    assert machine["account_sync_status"] == "SUCCESS"
    assert machine["last_account_check_at"]


def test_machine_health_automatically_caches_detailed_account_alerts(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "health",
        {
            "success": True,
            "health": {
                "machine_id": "PC001",
                "role": "executor",
                "project_version": "0.1.0",
                "healthy": True,
                "paused": False,
                "account_slots": {
                    "count": 1,
                    "items": [remote_account(status="LOGIN_REQUIRED")],
                    "error": "",
                },
            },
        },
    )

    checked = manager.check_machine("PC001")
    accounts = manager.list_accounts("PC001")
    alerts = manager.list_account_alerts("PC001")

    assert checked["success"] is True
    assert accounts["accounts"][0]["health_state"] == "BLOCKED"
    assert alerts["alerts"][0]["account_key"] == "PC001:brand-a"


def test_account_status_doctor_and_recovery_actions_refresh_cached_snapshot(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "account-status",
        {"success": True, "account": remote_account()},
    )
    transport.queue(
        "account-doctor",
        {
            "success": True,
            "account": remote_account(),
            "diagnosis": {"success": True, "status": "READY"},
        },
    )
    transport.queue(
        "account-action",
        {
            "success": True,
            "account": remote_account(),
            "operation": {"success": True, "setup": {"phase": "CONNECTION_READY"}},
        },
    )

    status = manager.get_machine_account_status("PC001", "brand-a")
    doctor = manager.doctor_machine_account("PC001", "brand-a")
    action = manager.run_machine_account_action("PC001", "brand-a", "profile-start")

    assert status["account"]["health_state"] == "READY"
    assert doctor["diagnosis"]["status"] == "READY"
    assert action["operation"]["setup"]["phase"] == "CONNECTION_READY"
    assert transport.calls[-1] == (
        "account-action",
        {"account": "brand-a", "account_action": "profile-start"},
    )


def test_account_management_is_preserved_across_worker_refreshes(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "list-accounts",
        {"success": True, "accounts": [remote_account()]},
        {"success": True, "accounts": [remote_account()]},
    )
    manager.list_machine_accounts("PC001")

    updated = manager.update_account_management(
        "PC001",
        "brand-a",
        {"account_note": "核心品牌账号", "scheduling_enabled": False},
    )
    manager.list_machine_accounts("PC001")
    account = manager.list_accounts("PC001")["accounts"][0]

    assert updated["account"]["account_note"] == "核心品牌账号"
    assert updated["account"]["scheduling_enabled"] is False
    assert account["account_note"] == "核心品牌账号"
    assert account["scheduling_enabled"] is False
    assert account["management_source_label"] == "经理机配置"
    assert account["management_updated_at"]


def test_disabled_account_cannot_receive_a_new_task(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue("list-accounts", {"success": True, "accounts": [remote_account()]})
    manager.list_machine_accounts("PC001")
    manager.update_account_management(
        "PC001",
        "brand-a",
        {"account_note": "暂停运营", "scheduling_enabled": False},
    )

    with pytest.raises(ServiceError, match="暂停调度") as exc_info:
        manager.dispatch_task(task_request())

    assert exc_info.value.code == "ACCOUNT_SCHEDULING_DISABLED"
    assert manager.list_tasks()["tasks"] == []


def test_manager_requests_worker_account_field_action_and_caches_status(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "account-field-request",
        {
            "success": True,
            "account": {
                **remote_account(status="LOGIN_REQUIRED"),
                "field_request": {
                    "request_id": "ACCOUNT-001",
                    "request_type": "LOGIN",
                    "state": "PENDING",
                    "instructions": "请登录并核对 UID",
                },
            },
            "field_request": {"request_id": "ACCOUNT-001", "state": "PENDING"},
        },
    )

    requested = manager.request_machine_account_field_action(
        "PC001",
        "brand-a",
        {
            "request_id": "ACCOUNT-001",
            "request_type": "LOGIN",
            "instructions": "请登录并核对 UID",
        },
    )
    account = manager.list_accounts("PC001")["accounts"][0]

    assert requested["account"]["field_request"]["state"] == "PENDING"
    assert account["field_request"]["request_id"] == "ACCOUNT-001"
    assert transport.calls[-1] == (
        "account-field-request",
        {
            "account": "brand-a",
            "request_id": "ACCOUNT-001",
            "request_type": "LOGIN",
            "instructions": "请登录并核对 UID",
        },
    )


def test_account_transport_failure_marks_cached_status_unknown_without_repeating(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "list-accounts",
        {"success": True, "accounts": [remote_account()]},
        ManagerTransportError("OFFLINE", "员工机离线"),
    )
    manager.list_machine_accounts("PC001")

    offline = manager.list_machine_accounts("PC001")

    assert offline["success"] is False
    assert offline["status"] == "UNKNOWN"
    assert offline["accounts"][0]["health_state"] == "UNKNOWN"
    assert manager.list_account_alerts()["alerts"][0]["severity"] == "UNKNOWN"
    machine = manager.list_machines()["machines"][0]
    assert machine["account_sync_status"] == "UNKNOWN"
    assert machine["account_sync_error_code"] == "OFFLINE"
    assert [call[0] for call in transport.calls].count("list-accounts") == 2


def test_sync_status_is_saved_by_cycle_kind(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)

    manager.record_sync_status(
        "health_accounts",
        {"status": "SUCCESS", "completed_at": "2026-09-02T00:00:00+00:00"},
    )
    manager.record_sync_status(
        "results",
        {"status": "IDLE", "completed_at": "2026-09-02T00:00:01+00:00"},
    )

    sync = manager.list_sync_status()["sync"]
    assert sync["health_accounts"]["status"] == "SUCCESS"
    assert sync["results"]["status"] == "IDLE"


def test_dashboard_snapshot_aggregates_manager_cache_without_transport(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    manager.record_sync_status(
        "health_accounts",
        {"status": "SUCCESS", "completed_at": "2026-09-04T00:00:00+00:00"},
    )

    snapshot = manager.dashboard_snapshot()

    assert snapshot["success"] is True
    assert snapshot["fleet"] == {
        "machine_total": 1,
        "ready_total": 0,
        "account_total": 0,
    }
    assert snapshot["machines"][0]["machine_id"] == "PC001"
    assert snapshot["collection"]["health_accounts"]["status"] == "SUCCESS"
    assert snapshot["data_sources"][0]["updated_at"]
    assert snapshot["agent_messages"] == []
    assert snapshot["result_history"] == []
    assert transport.calls == []


def test_dashboard_execution_count_tracks_receipts_not_paused_or_history(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    states = [
        ("PAUSED", ""),
        ("PENDING_DISPATCH", ""),
        ("DISPATCH_UNKNOWN", ""),
        ("ACCEPTED", ""),
        ("ACKNOWLEDGED", ""),
        ("RUNNING", "RUNNING"),
        ("PAUSE_REQUESTED", ""),
        ("RUNNING", "SUCCESS"),
        ("COMPLETED", "FAILED"),
    ]
    for index, (dispatch_state, business_state) in enumerate(states):
        manager.store.put(
            "tasks",
            f"task-{index}",
            {
                "task_id": f"task-{index}",
                "created_at": "2026-09-07T00:00:00+00:00",
                "state": business_state or dispatch_state,
                "dispatch_state": dispatch_state,
                "business_state": business_state,
            },
        )

    snapshot = manager.dashboard_snapshot()

    assert snapshot["overall"]["active_task_total"] == 4
    assert snapshot["overall"]["reconciliation_total"] == 1
    assert len(snapshot["tasks"]) == len(states)
    assert transport.calls == []


def test_result_snapshot_separates_current_revisions_from_audit_history(
    tmp_path: Path,
) -> None:
    manager, _transport = service(tmp_path)
    records = [
        {
            "record_id": "result-1-v1",
            "machine_id": "PC001",
            "task_id": "task-1",
            "remote_execution_id": "execution-1",
            "result_revision": 1,
            "sequence": 1,
            "state": "RESULT_UNKNOWN",
            "collected_at": "2026-09-04T00:00:01+00:00",
        },
        {
            "record_id": "result-1-v2",
            "machine_id": "PC001",
            "task_id": "task-1",
            "remote_execution_id": "execution-1",
            "result_revision": 2,
            "sequence": 2,
            "state": "SUCCESS",
            "collected_at": "2026-09-04T00:00:02+00:00",
        },
        {
            "record_id": "result-2-v1",
            "machine_id": "PC001",
            "task_id": "task-2",
            "remote_execution_id": "execution-2",
            "result_revision": 1,
            "sequence": 3,
            "state": "SUCCESS",
            "collected_at": "2026-09-04T00:00:03+00:00",
        },
    ]
    for record in records:
        manager.store.put("results", record["record_id"], record)

    payload = manager.list_results()
    snapshot = manager.dashboard_snapshot()

    assert len(payload["results"]) == 3
    assert {item["record_id"] for item in payload["current_results"]} == {
        "result-1-v2",
        "result-2-v1",
    }
    assert len(payload["result_history"]) == 3
    assert sum(item["is_current_revision"] for item in payload["result_history"]) == 2
    assert [item["record_id"] for item in snapshot["results"]] == [
        item["record_id"] for item in payload["current_results"]
    ]
    assert len(snapshot["result_history"]) == 3


def test_stale_machine_is_not_reported_ready_and_dispatch_rechecks_health(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.store.update(
        "machines",
        "PC001",
        lambda current: {**current, "last_check_at": "2020-01-01T00:00:00+00:00"},
    )

    cached = manager.list_machines()["machines"][0]
    dispatched = manager.dispatch_task(task_request())

    assert cached["stored_status"] == "READY"
    assert cached["status"] == "UNKNOWN"
    assert cached["data_freshness"] == "STALE"
    assert cached["stale"] is True
    assert dispatched["success"] is True
    assert [call[0] for call in transport.calls][-3:] == [
        "health",
        "accept-task",
        "start-task",
    ]


def test_stale_account_becomes_unknown_and_gets_transient_alert(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "list-accounts",
        {"success": True, "accounts": [remote_account()]},
    )
    manager.list_machine_accounts("PC001")
    manager.store.update(
        "accounts",
        "PC001:brand-a",
        lambda current: {**current, "updated_at": "2020-01-01T00:00:00+00:00"},
    )

    cached = manager.list_accounts("PC001")
    alerts = manager.list_account_alerts("PC001")

    assert cached["summary"]["READY"] == 0
    assert cached["summary"]["UNKNOWN"] == 1
    assert cached["accounts"][0]["stored_health_state"] == "READY"
    assert cached["accounts"][0]["data_freshness"] == "STALE"
    assert alerts["total"] == 1
    assert alerts["alerts"][0]["severity"] == "UNKNOWN"
    assert alerts["alerts"][0]["transient"] is True


def test_register_machine_is_persistent_and_exact_duplicate_is_idempotent(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)

    first = manager.register_machine(machine_values())
    second = manager.register_machine(machine_values())
    reloaded = ManagerService(
        store=ManagerStore(tmp_path / "manager"),
        transport=FakeTransport(),  # type: ignore[arg-type]
    )

    assert first["status"] == "REGISTERED"
    assert first["machine"]["record_id"] == "machine:PC001"
    assert first["machine"]["manager_id"] == "MILA-MANAGER"
    assert first["machine"]["ssh_state"] == "UNVERIFIED"
    assert second["status"] == "ALREADY_REGISTERED"
    assert reloaded.list_machines()["machines"][0]["machine_id"] == "PC001"


def test_machine_id_is_normalized_across_registration_and_health(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    registered = manager.register_machine(machine_values(machine_id="executor-01"))
    transport.queue(
        "health",
        {
            "health": {
                "machine_id": "EXECUTOR-01",
                "role": "executor",
                "project_version": "0.1.0",
                "healthy": True,
            }
        },
    )

    checked = manager.check_machine("executor-01")

    assert registered["machine"]["machine_id"] == "EXECUTOR-01"
    assert checked["status"] == "READY"


def test_check_machine_adopts_worker_exchange_paths_for_legacy_registration(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "health",
        {
            "health": {
                "machine_id": "PC001",
                "role": "executor",
                "project_version": "0.1.0",
                "healthy": True,
                "task_exchange": {
                    "transport": "sftp",
                    "inbox_root": r"C:\auto-xhs\manager-task-inbox",
                    "outbox_root": r"C:\auto-xhs\manager-task-outbox",
                },
            }
        },
    )

    checked = manager.check_machine("PC001")

    assert checked["machine"]["task_inbox_root"] == r"C:\auto-xhs\manager-task-inbox"
    assert checked["machine"]["task_outbox_root"] == r"C:\auto-xhs\manager-task-outbox"


def test_register_rejects_duplicate_id_or_endpoint(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.register_machine(machine_values())

    with pytest.raises(ServiceError) as duplicate_id:
        manager.register_machine(machine_values(host="192.168.1.22"))
    with pytest.raises(ServiceError) as duplicate_endpoint:
        manager.register_machine(machine_values(machine_id="PC002"))

    assert duplicate_id.value.code == "DUPLICATE_MACHINE_ID"
    assert duplicate_endpoint.value.code == "DUPLICATE_MACHINE_ENDPOINT"


def test_check_machine_validates_role_identity_version_and_offline(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "health",
        {
            "health": {
                "machine_id": "PC999",
                "role": "executor",
                "project_version": "0.1.0",
                "healthy": True,
            }
        },
        {
            "health": {
                "machine_id": "PC001",
                "role": "manager",
                "project_version": "0.1.0",
                "healthy": True,
            }
        },
        {
            "health": {
                "machine_id": "PC001",
                "role": "executor",
                "project_version": "2.0.0",
                "healthy": True,
            }
        },
        ManagerTransportError("OFFLINE", "连接失败"),
    )

    assert manager.check_machine("PC001")["machine"]["last_error_code"] == ("MACHINE_ID_MISMATCH")
    assert manager.check_machine("PC001")["machine"]["last_error_code"] == ("MACHINE_ROLE_MISMATCH")
    assert manager.check_machine("PC001")["machine"]["last_error_code"] == (
        "PROJECT_VERSION_MISMATCH"
    )
    assert manager.check_machine("PC001")["status"] == "OFFLINE"


def test_check_machine_reports_remote_pause_and_degraded_health(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "health",
        {
            "machine_id": "PC001",
            "role": "executor",
            "project_version": "0.1.0",
            "health": "PAUSED",
            "paused": True,
        },
        {
            "machine_id": "PC001",
            "role": "executor",
            "project_version": "0.1.0",
            "health": "DEGRADED",
            "paused": False,
        },
    )

    paused = manager.check_machine("PC001")
    degraded = manager.check_machine("PC001")

    assert paused["success"] is True
    assert paused["status"] == "PAUSED"
    assert degraded["success"] is False
    assert degraded["machine"]["last_error_code"] == "EXECUTOR_UNHEALTHY"


def test_manager_controls_remote_executor_service_and_records_ready_health(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())

    status = manager.machine_executor_status("PC001")
    started = manager.start_machine_executor("PC001")
    restarted = manager.restart_machine_executor("PC001")

    assert status["status"] == "READY"
    assert status["executor_service"] == {"ready": True}
    assert started["status"] == "READY"
    assert started["machine"]["ssh_state"] == "ONLINE"
    assert started["machine"]["last_health"]["local_service"]["ready"] is True
    assert restarted["status"] == "READY"
    assert [call[0] for call in transport.calls] == [
        "executor-status",
        "start-executor",
        "restart-executor",
    ]


def test_remote_executor_timeout_is_recorded_without_repeating_action(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue(
        "start-executor",
        ManagerTransportError("RESULT_UNKNOWN", "启动结果未确认", result_unknown=True),
    )

    result = manager.start_machine_executor("PC001")

    assert result["success"] is False
    assert result["status"] == "CHECK_UNKNOWN"
    assert result["machine"]["ssh_state"] == "CHECK_UNKNOWN"
    assert [call[0] for call in transport.calls] == ["start-executor"]


def test_offline_dispatch_stays_pending_and_is_not_business_failure(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("health", ManagerTransportError("OFFLINE", "连接超时"))

    result = manager.dispatch_task(task_request())

    assert result["status"] == "PENDING_DISPATCH"
    assert result["task"]["business_state"] == ""
    assert result["task"]["reconciliation_required"] is False


def test_pending_task_with_legacy_empty_operation_resumes_with_default_operation(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("health", ManagerTransportError("OFFLINE", "连接超时"))
    pending = manager.dispatch_task(task_request())
    manager.store.update(
        "tasks",
        "task-1",
        lambda current: {
            **current,
            "request": {**current["request"], "operation": ""},
        },
    )

    resumed = manager.dispatch_task(task_request())

    assert pending["status"] == "PENDING_DISPATCH"
    assert resumed["status"] == "TASK_ACK"
    envelope = next(payload for action, payload in transport.calls if action == "accept-task")
    assert envelope["operation"] is None
    assert resumed["task"]["request"]["operation"] is None


def test_dispatch_accepts_starts_and_records_fast_ack(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)

    result = manager.dispatch_task(task_request())

    assert result["status"] == "TASK_ACK"
    assert result["task"]["dispatch_state"] == "COMPLETED"
    assert result["task"]["business_state"] == "SUCCESS"
    assert result["task"]["state"] == "SUCCESS"
    assert result["task"]["actual_version"] == "0.1.0"
    assert result["task"]["message"] == "页面回读成功"
    assert result["task"]["result_summary"] == "页面回读成功"
    assert [action for action, _payload in transport.calls][-2:] == [
        "accept-task",
        "start-task",
    ]
    envelope = next(payload for action, payload in transport.calls if action == "accept-task")
    assert envelope["operation"] is None
    assert result["task"]["request"]["operation"] is None


def test_sftp_dispatch_retries_only_ssh_wake_after_task_was_transferred(tmp_path: Path) -> None:
    class ExchangeTransport(FakeTransport):
        def sftp_upload_atomic(
            self,
            _machine: dict,
            source: str,
            remote_path: str,
            *,
            timeout_seconds: float | None = None,
        ) -> dict:
            self.uploads.append((source, remote_path))
            return {"success": True, "timeout_seconds": timeout_seconds}

        def sftp_download(self, *_args, **_kwargs) -> dict:
            return {"success": True}

        def invoke(
            self,
            machine: dict,
            action: str,
            payload: dict,
            *,
            timeout_seconds: float | None = None,
        ) -> dict:
            if action != "wake-task":
                return super().invoke(
                    machine,
                    action,
                    payload,
                    timeout_seconds=timeout_seconds,
                )
            self.calls.append((action, payload))
            queued = self.responses[action]
            if queued:
                response = queued.pop(0)
                if isinstance(response, Exception):
                    raise response
                return response
            return {
                "success": True,
                "status": "TASK_ACK",
                "execution": {
                    "remote_execution_id": "execution-1",
                    "state": "ACCEPTED",
                    "project_version": "0.1.0",
                },
            }

    transport = ExchangeTransport()
    manager = ManagerService(
        store=ManagerStore(tmp_path / "manager"),
        transport=transport,  # type: ignore[arg-type]
    )
    manager.register_machine(
        {
            **machine_values(),
            "task_inbox_root": r"C:\auto-xhs\manager-task-inbox",
            "task_outbox_root": r"C:\auto-xhs\manager-task-outbox",
        }
    )
    manager.check_machine("PC001")
    transport.queue("wake-task", ManagerTransportError("OFFLINE", "员工机暂时离线"))

    first = manager.dispatch_task(task_request())
    second = manager.dispatch_task(task_request())

    assert first["status"] == "TRANSFERRED"
    assert second["status"] == "TASK_ACK"
    assert second["task"]["dispatch_state"] == "ACKNOWLEDGED"
    assert len(transport.uploads) == 1
    assert [action for action, _payload in transport.calls].count("wake-task") == 2


def test_dispatch_sends_manager_scope_and_records_pinned_function_version(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    request = {
        **task_request(),
        "function_id": "EXPLORE-02",
        "allowed_function_versions": ["1.1", "1.0"],
    }
    transport.queue(
        "accept-task",
        {
            "success": True,
            "execution": {
                "remote_execution_id": "execution-versioned",
                "state": "ACCEPTED",
                "function_id": "EXPLORE-02",
                "project_version": "0.1.0",
                "actual_version": "1.1",
                "version_status": "TRIAL",
            },
        },
    )
    transport.queue(
        "start-task",
        {
            "success": True,
            "execution": {
                "remote_execution_id": "execution-versioned",
                "state": "SUCCESS",
                "function_id": "EXPLORE-02",
                "project_version": "0.1.0",
                "actual_version": "1.1",
                "version_status": "TRIAL",
                "result_summary": "页面回读成功",
            },
        },
    )

    result = manager.dispatch_task(request)

    envelope = next(payload for action, payload in transport.calls if action == "accept-task")
    assert envelope["manager_id"] == "MILA-MANAGER"
    assert envelope["function_id"] == "EXPLORE-02"
    assert envelope["allowed_function_versions"] == ["1.1", "1.0"]
    assert result["task"]["project_version"] == "0.1.0"
    assert result["task"]["actual_version"] == "1.1"
    assert result["task"]["version_status"] == "TRIAL"


def test_versioned_dispatch_requires_an_allowed_function_version(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.register_machine(machine_values())

    with pytest.raises(ServiceError) as invalid:
        manager.dispatch_task({**task_request(), "function_id": "EXPLORE-02"})

    assert invalid.value.code == "INVALID_TASK"


def test_repeated_completed_dispatch_does_not_create_or_run_again(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.dispatch_task(task_request())
    call_count = len(transport.calls)

    repeated = manager.dispatch_task(task_request())

    assert repeated["status"] == "IDEMPOTENT"
    assert len(transport.calls) == call_count
    assert len(manager.list_tasks()["tasks"]) == 1


def test_accept_timeout_requires_reconciliation_before_resend(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "accept-task",
        ManagerTransportError("RESULT_UNKNOWN", "响应超时", result_unknown=True),
    )

    first = manager.dispatch_task(task_request())
    second = manager.dispatch_task(task_request())

    assert first["status"] == "DISPATCH_UNKNOWN"
    assert second["status"] == "RECONCILIATION_REQUIRED"
    assert first["task"]["business_state"] == ""


def test_start_timeout_keeps_remote_execution_and_requires_reconciliation(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "start-task",
        ManagerTransportError("RESULT_UNKNOWN", "运行超时", result_unknown=True),
    )

    result = manager.dispatch_task(task_request())

    assert result["status"] == "RECONCILIATION_REQUIRED"
    assert result["task"]["remote_execution_id"] == "execution-1"
    assert result["task"]["business_state"] == ""


def test_reconcile_not_found_allows_safe_redispatch(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "accept-task",
        ManagerTransportError("RESULT_UNKNOWN", "响应超时", result_unknown=True),
    )
    manager.dispatch_task(task_request())
    transport.queue(
        "task-status",
        {
            "success": False,
            "error": {"code": "TASK_NOT_FOUND", "message": "未接收"},
        },
    )

    result = manager.reconcile_task("task-1")

    assert result["status"] == "PENDING_DISPATCH"
    assert result["task"]["reconciliation_required"] is False


def test_remote_worker_error_is_not_treated_as_accepted_task(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    transport.queue(
        "accept-task",
        {
            "success": False,
            "error": {
                "code": "PROJECT_VERSION_NOT_ALLOWED",
                "message": "执行机版本不在允许范围",
            },
        },
    )

    result = manager.dispatch_task(task_request())

    assert result["status"] == "BLOCKED"
    assert result["task"]["business_state"] == ""
    assert result["task"]["error_code"] == "PROJECT_VERSION_NOT_ALLOWED"
    assert [action for action, _payload in transport.calls].count("start-task") == 0


def test_local_pending_task_pause_and_resume_are_idempotent(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("health", ManagerTransportError("OFFLINE", "离线"))
    manager.dispatch_task(task_request())

    paused = manager.pause_task("task-1")
    paused_again = manager.pause_task("task-1")
    call_count = len(transport.calls)
    repeated_dispatch = manager.dispatch_task(task_request())
    resumed = manager.resume_task("task-1")

    assert paused["status"] == "PAUSED"
    assert paused_again["status"] == "PAUSED"
    assert repeated_dispatch["status"] == "PAUSED"
    assert len(transport.calls) == call_count
    assert resumed["status"] == "PENDING_DISPATCH"


def test_task_authorization_rejects_private_credentials(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    request = task_request()
    request["authorization"] = {"approval_id": "approval-1", "session_token": "secret"}

    with pytest.raises(ServiceError) as error:
        manager.dispatch_task(request)

    assert error.value.code == "PRIVATE_CREDENTIAL_NOT_ALLOWED"


def test_machine_pause_is_remembered_while_offline_and_blocks_new_dispatch(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    transport.queue("pause-machine", ManagerTransportError("OFFLINE", "离线"))

    pause = manager.pause_machine("PC001")
    task = manager.dispatch_task(task_request())

    assert pause["status"] == "PAUSE_PENDING"
    assert pause["machine"]["desired_paused"] is True
    assert task["status"] == "PAUSED"


def test_persona_goal_schedule_and_recovery_keep_versioned_context(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)

    created_persona = manager.save_persona(persona_request())["persona"]
    revised_persona = manager.save_persona(
        {
            **persona_request(),
            "persona_id": created_persona["persona_id"],
            "tone": "自然、克制、有具体场景",
        }
    )["persona"]
    saved_goal = manager.save_goal(goal_request())["goal"]

    assert created_persona["version"] == 1
    assert revised_persona["version"] == 2
    assert len(revised_persona["versions"]) == 2
    assert saved_goal["state"] == "DRAFT"
    assert manager.list_tasks()["tasks"] == []
    assert manager.list_schedules()["schedules"] == []

    active_goal = manager.activate_goal(saved_goal["goal_id"])["goal"]
    schedule_values = {
        **schedule_request(),
        "goal_id": saved_goal["goal_id"],
    }
    schedule = manager.register_schedule(schedule_values)["schedule"]
    dispatched = manager.dispatch_task({**task_request(), "schedule_id": schedule["schedule_id"]})

    assert active_goal["state"] == "ACTIVE"
    assert active_goal["persona_bindings"][0]["version"] == 2
    assert schedule["goal_snapshot"]["goal_id"] == saved_goal["goal_id"]
    assert schedule["persona_snapshot"]["version"] == 2
    assert dispatched["task"]["request"]["goal_id"] == saved_goal["goal_id"]
    assert dispatched["task"]["request"]["persona_snapshot"]["version"] == 2

    transport.queue(
        "collect-results",
        {
            "success": True,
            "results": [
                {
                    "sequence": 1,
                    "result_revision": 1,
                    "task_id": "task-1",
                    "remote_execution_id": "execution-1",
                    "state": "SUCCESS",
                    "actual_count": 7,
                    "goal_id": "GOAL-REMOTE-STALE",
                    "persona_id": "PERSONA-REMOTE-STALE",
                    "item_results": [],
                    "incremental_data": [{"record_type": "operation", "record_id": "goal-event-1"}],
                }
            ],
            "next_sequence": 1,
        },
    )
    manager.collect_results("PC001", download_evidence=False)
    stored = manager.list_results("PC001")
    result = stored["results"][0]
    incremental = stored["incremental_records"][0]
    goal = manager.get_goal(saved_goal["goal_id"])["goal"]

    assert result["goal_id"] == saved_goal["goal_id"]
    assert result["goal_version"] == 1
    assert result["persona_id"] == revised_persona["persona_id"]
    assert result["persona_version"] == 2
    assert incremental["goal_id"] == saved_goal["goal_id"]
    assert incremental["persona_version"] == 2
    assert goal["progress"]["target_total"] == 32
    assert goal["progress"]["actual_total"] == 7
    assert goal["progress"]["actual_metrics"] == {"browse": 7}


def test_goal_activation_requires_the_target_account_persona(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    goal = manager.save_goal(goal_request())["goal"]

    with pytest.raises(ServiceError) as captured:
        manager.activate_goal(goal["goal_id"])

    assert captured.value.code == "PERSONA_REQUIRED"


def test_home_engagement_progress_counts_browse_and_like_separately(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.save_persona(persona_request())
    goal = manager.save_goal(goal_request())["goal"]
    manager.activate_goal(goal["goal_id"])
    manager.store.put(
        "results",
        "result:home-engagement",
        {
            "record_id": "result:home-engagement",
            "goal_id": goal["goal_id"],
            "capability": "home-engagement",
            "task_request": {
                "capability": "home-engagement",
                "parameters": {"browse_count": 2, "like_count": 1},
            },
            "state": "SUCCESS",
            "item_results_status": "observed",
            "item_results": [
                {
                    "feed_id": "feed-1",
                    "browse": {"status": "success"},
                    "like": {"status": "success", "success": True},
                },
                {
                    "feed_id": "feed-2",
                    "browse": {"status": "success"},
                    "like": None,
                },
            ],
        },
    )

    progress = manager.get_goal(goal["goal_id"])["goal"]["progress"]

    assert progress["actual_metrics"] == {"browse": 2, "like": 1}
    assert progress["actual_total"] == 3
    stored = manager.store.get("results", "result:home-engagement")
    assert manager._result_target_count(stored) == 3
    assert manager._result_actual_count(stored) == 3


def test_webui_operation_task_is_claimed_planned_dispatched_and_reconciled(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.save_persona(persona_request())
    goal = manager.save_goal(goal_request())["goal"]
    manager.activate_goal(goal["goal_id"])
    calls_before_submit = len(transport.calls)

    submitted = manager.create_operation_task(operation_task_request(goal["goal_id"]))
    operation_task = submitted["operation_task"]

    assert submitted["status"] == "WAITING_AGENT"
    assert operation_task["goal_snapshot"]["goal_id"] == goal["goal_id"]
    assert operation_task["persona_bindings"][0]["version"] == 1
    assert len(transport.calls) == calls_before_submit
    assert manager.list_tasks()["tasks"] == []
    assert manager.list_schedules()["schedules"] == []

    schedule_values = {
        **schedule_request(),
        "operation_task_id": operation_task["operation_task_id"],
    }
    with pytest.raises(ServiceError) as not_claimed:
        manager.register_schedule(schedule_values)
    assert not_claimed.value.code == "OPERATION_TASK_NOT_CLAIMED"

    claimed = manager.claim_operation_task(
        operation_task["operation_task_id"],
        {"manager_agent_id": "manager-agent-01"},
    )
    planned = manager.record_operation_plan(
        operation_task["operation_task_id"],
        {
            "manager_agent_id": "manager-agent-01",
            "plan_summary": "每天采集并按目标节奏执行",
            "planned_actions": [{"capability": "search-feeds", "daily": 1}],
        },
    )
    schedule = manager.register_schedule(schedule_values)["schedule"]
    dispatched = manager.dispatch_task({**task_request(), "schedule_id": schedule["schedule_id"]})

    assert claimed["status"] == "PLANNING"
    assert planned["operation_task"]["plan"]["planned_actions"][0]["daily"] == 1
    assert schedule["operation_task_id"] == operation_task["operation_task_id"]
    assert dispatched["task"]["request"]["operation_task_id"] == operation_task["operation_task_id"]
    running = manager.get_operation_task(operation_task["operation_task_id"])["operation_task"]
    assert running["state"] == "RUNNING"
    assert running["schedule_ids"] == ["schedule-1"]
    assert running["execution_task_ids"] == ["task-1"]
    assert running["progress"]["plan_action_count"] == 1

    transport.queue(
        "collect-results",
        {
            "success": True,
            "results": [
                {
                    "sequence": 1,
                    "result_revision": 1,
                    "task_id": "task-1",
                    "remote_execution_id": "execution-1",
                    "state": "SUCCESS",
                    "actual_count": 5,
                    "item_results": [],
                    "incremental_data": [
                        {"record_type": "operation", "record_id": "operation-event-1"}
                    ],
                }
            ],
            "next_sequence": 1,
        },
    )
    manager.collect_results("PC001", download_evidence=False)
    results = manager.list_results("PC001")
    reconciled = manager.get_operation_task(operation_task["operation_task_id"])["operation_task"]

    assert results["results"][0]["operation_task_id"] == operation_task["operation_task_id"]
    assert (
        results["incremental_records"][0]["operation_task_id"]
        == operation_task["operation_task_id"]
    )
    assert reconciled["progress"]["actual_total"] == 5
    assert len(reconciled["result_ids"]) == 1

    finished = manager.finish_operation_task(
        operation_task["operation_task_id"],
        {
            "manager_agent_id": "manager-agent-01",
            "state": "COMPLETED",
            "result_summary": "员工机结果已回收并完成对账",
        },
    )
    assert finished["operation_task"]["state"] == "COMPLETED"
    assert manager.list_schedules()["schedules"][0]["state"] == "PAUSED"
    with pytest.raises(ServiceError) as completed:
        manager.dispatch_task(
            {**task_request(), "task_id": "task-2", "schedule_id": schedule["schedule_id"]}
        )
    assert completed.value.code == "SCHEDULE_PAUSED"


def test_persona_cannot_move_onto_another_active_account_binding(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    first = manager.save_persona(persona_request())["persona"]
    manager.save_persona({**persona_request(), "account_slot": "brand-b"})

    with pytest.raises(ServiceError) as captured:
        manager.save_persona(
            {
                **persona_request(),
                "persona_id": first["persona_id"],
                "account_slot": "brand-b",
            }
        )

    assert captured.value.code == "PERSONA_BINDING_EXISTS"


def test_agent_schedule_is_idempotent_and_tracks_terminal_task(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    register_ready(manager)

    registered = manager.register_schedule(schedule_request())
    repeated = manager.register_schedule(schedule_request())
    paused = manager.pause_schedule("schedule-1")

    scheduled_task = task_request()
    scheduled_task["schedule_id"] = "schedule-1"
    with pytest.raises(ServiceError) as error:
        manager.dispatch_task(scheduled_task)
    assert error.value.code == "SCHEDULE_PAUSED"

    resumed = manager.resume_schedule("schedule-1")
    dispatched = manager.dispatch_task(scheduled_task)
    schedule = manager.list_schedules()["schedules"][0]

    assert registered["status"] == "REGISTERED"
    assert repeated["status"] == "IDEMPOTENT"
    assert paused["schedule"]["state"] == "PAUSED"
    assert resumed["schedule"]["state"] == "ACTIVE"
    assert dispatched["status"] == "TASK_ACK"
    assert schedule["last_task_id"] == "task-1"
    assert schedule["last_result_state"] == "SUCCESS"
    assert schedule["recent_runs"][0]["task_id"] == "task-1"


def test_schedule_requires_unknown_result_reconciliation_before_next_dispatch(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.register_schedule(schedule_request())
    first = task_request()
    first["schedule_id"] = "schedule-1"
    transport.queue(
        "start-task",
        ManagerTransportError("RESULT_UNKNOWN", "回执中断", result_unknown=True),
    )

    unknown = manager.dispatch_task(first)
    second = {**task_request(), "task_id": "task-2", "schedule_id": "schedule-1"}

    with pytest.raises(ServiceError) as error:
        manager.dispatch_task(second)

    assert unknown["status"] == "RECONCILIATION_REQUIRED"
    assert error.value.code == "SCHEDULE_RECONCILIATION_REQUIRED"


def test_manager_report_preserves_missing_values_and_revision_history(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.register_machine(machine_values())
    manager.store.put(
        "results",
        "result-1",
        {
            "record_id": "result-1",
            "machine_id": "PC001",
            "account_slot": "brand-a",
            "capability": "search-feeds",
            "state": "SUCCESS",
            "item_results_status": "missing",
            "item_results_missing_reason": "未提供逐项结果",
            "incremental_data_status": "missing",
            "incremental_data_missing_reason": "未提供增量数据",
            "collected_at": "2026-09-03T01:00:00+00:00",
        },
    )
    request = {
        "report_type": "daily",
        "machine_id": "PC001",
        "account_slot": "brand-a",
        "start_at": "2026-09-03T00:00:00Z",
        "end_at": "2026-09-03T23:59:59Z",
    }

    original = manager.generate_operation_report(request)["report"]
    revision = manager.revise_operation_report(
        original["report_id"],
        {"revision_reason": "补充回收结果"},
    )["report"]
    stored_original = manager.store.get("operation_reports", original["report_id"])

    assert original["data_status"] == "mixed"
    assert original["content"]["target_total"] is None
    assert original["content"]["actual_total"] is None
    assert original["content"]["data_gap_count"] == 3
    assert {gap["data_type"] for gap in original["data_gaps"]} == {
        "item_results",
        "business_result",
        "incremental_data",
    }
    assert revision["report_id"] != original["report_id"]
    assert revision["root_report_id"] == original["report_id"]
    assert revision["previous_report_id"] == original["report_id"]
    assert revision["revision_number"] == 2
    assert stored_original == original
    assert len(manager.list_operation_reports()["reports"]) == 2


def test_function_package_transfer_prepares_uploads_finalizes_and_is_idempotent(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    package = tmp_path / "EXPLORE-01"
    package.mkdir()
    (package / "manifest.json").write_text('{"version":"1.0"}', encoding="utf-8")
    request = {
        "transfer_id": "transfer-1",
        "machine_id": "PC001",
        "transfer_type": "function_package",
        "source_path": str(package.resolve()),
        "allowed_versions": ["0.1.0"],
    }

    first = manager.transfer(request)
    call_count = len(transport.calls)
    second = manager.transfer(request)

    assert first["status"] == "SUCCESS"
    assert first["transfer"]["actual_version"] == "1.0"
    assert first["transfer"]["remote_path"].endswith(r"EXPLORE-01\1.0")
    assert transport.uploads[0][0] == str(package.resolve())
    assert second["status"] == "IDEMPOTENT"
    assert len(transport.calls) == call_count


def test_transfer_timeout_is_not_reported_as_installed(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    source = tmp_path / "task.json"
    source.write_text("{}", encoding="utf-8")
    transport.queue(
        "prepare-transfer",
        ManagerTransportError("RESULT_UNKNOWN", "准备超时", result_unknown=True),
    )

    result = manager.transfer(
        {
            "transfer_id": "transfer-2",
            "machine_id": "PC001",
            "transfer_type": "task_package",
            "source_path": str(source.resolve()),
        }
    )

    assert result["status"] == "RESULT_UNKNOWN"
    assert result["transfer"]["actual_version"] == ""


def test_list_transfers_returns_newest_first(tmp_path: Path) -> None:
    manager, _transport = service(tmp_path)
    manager.store.put(
        "transfers",
        "transfer-old",
        {"transfer_id": "transfer-old", "created_at": "2026-08-30T00:00:00+00:00"},
    )
    manager.store.put(
        "transfers",
        "transfer-new",
        {"transfer_id": "transfer-new", "created_at": "2026-08-31T00:00:00+00:00"},
    )

    result = manager.list_transfers()

    assert result["success"] is True
    assert [item["transfer_id"] for item in result["transfers"]] == [
        "transfer-new",
        "transfer-old",
    ]


def test_transfer_retry_reconciles_remote_success_without_uploading_again(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    source = tmp_path / "task.json"
    source.write_text("{}", encoding="utf-8")
    request = {
        "transfer_id": "transfer-remote-success",
        "machine_id": "PC001",
        "transfer_type": "task_package",
        "source_path": str(source.resolve()),
    }
    transport.queue(
        "finalize-transfer",
        ManagerTransportError("RESULT_UNKNOWN", "回执超时", result_unknown=True),
    )

    first = manager.transfer(request)
    transport.queue(
        "prepare-transfer",
        {
            "success": True,
            "transfer": {
                "state": "SUCCESS",
                "actual_version": "transfer-remote-success",
                "installed_path": r"C:\worker\task-inbox\task\transfer-remote-success",
            },
        },
    )
    second = manager.transfer(request)

    assert first["status"] == "RESULT_UNKNOWN"
    assert second["status"] == "IDEMPOTENT"
    assert second["transfer"]["actual_version"] == "transfer-remote-success"
    assert len(transport.uploads) == 1


def test_remote_transfer_validation_failure_keeps_actual_version_empty(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    package = tmp_path / "bad-package"
    package.mkdir()
    transport.queue(
        "finalize-transfer",
        {
            "success": False,
            "transfer": {
                "state": "FAILED",
                "error_code": "MANIFEST_PROJECT_VERSION_MISMATCH",
                "message": "功能包与执行机版本不兼容",
            },
        },
    )

    result = manager.transfer(
        {
            "transfer_id": "transfer-rejected",
            "machine_id": "PC001",
            "transfer_type": "function_package",
            "source_path": str(package.resolve()),
        }
    )

    assert result["status"] == "FAILED"
    assert result["transfer"]["actual_version"] == ""
    assert result["transfer"]["error_code"] == "MANIFEST_PROJECT_VERSION_MISMATCH"


def test_version_list_refreshes_cache_and_offline_returns_last_known(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    remote_version = {
        "function_id": "EXPLORE-02",
        "version": "1.1",
        "status": "TRIAL",
        "scope": {
            "manager_ids": ["MILA-MANAGER"],
            "machine_ids": ["PC001"],
            "account_slots": ["brand-a"],
            "allowed_actions": ["search-feeds"],
        },
        "stable": False,
    }
    transport.queue(
        "list-function-versions",
        {
            "success": True,
            "functions": [{"function_id": "EXPLORE-02", "stable_version": "1.0"}],
            "versions": [remote_version],
        },
        ManagerTransportError("OFFLINE", "执行机离线"),
    )

    refreshed = manager.list_function_versions("PC001", "EXPLORE-02")
    offline = manager.list_function_versions("PC001", "EXPLORE-02")

    assert refreshed["source"] == "REMOTE"
    assert refreshed["versions"][0]["status"] == "TRIAL"
    assert offline["status"] == "OFFLINE"
    assert offline["source"] == "LAST_KNOWN"
    assert offline["versions"][0]["version"] == "1.1"


def test_set_function_version_policy_sends_explicit_scope_and_caches_result(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    manager.register_machine(machine_values())
    scope = {
        "manager_ids": ["MILA-MANAGER"],
        "machine_ids": ["PC001"],
        "account_slots": ["brand-a"],
        "allowed_actions": ["search-feeds"],
    }
    transport.queue(
        "set-function-version-policy",
        {
            "success": True,
            "version": {
                "function_id": "EXPLORE-02",
                "version": "1.0",
                "status": "FORMAL",
                "scope": scope,
                "stable": True,
            },
        },
    )

    result = manager.set_function_version_policy(
        {
            "machine_id": "PC001",
            "function_id": "EXPLORE-02",
            "version": "1.0",
            "status": "正式",
            "scope": scope,
            "set_as_stable": True,
        }
    )

    action, payload = transport.calls[-1]
    assert action == "set-function-version-policy"
    assert payload["status"] == "FORMAL"
    assert payload["scope"] == scope
    assert result["version"]["stable"] is True
    cached = manager.store.list("versions")
    assert cached[0]["function_id"] == "EXPLORE-02"


def test_collect_results_is_incremental_idempotent_and_downloads_evidence(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.dispatch_task(task_request())
    result_item = {
        "sequence": 1,
        "result_revision": 1,
        "task_id": "task-1",
        "remote_execution_id": "execution-1",
        "state": "SUCCESS",
        "result_summary": "页面已回读",
        "error_classification": "",
        "manual_action_required": False,
        "business_result": {
            "feeds": [{"id": "note-1", "displayTitle": "露营地点"}],
            "count": 1,
        },
        "business_result_status": "observed",
        "item_results": [
            {"target_id": "note-1", "state": "SUCCESS"},
            {
                "target_id": "note-2",
                "state": "BLOCKED",
                "error_classification": "ACCOUNT_REVIEW",
            },
        ],
        "evidence_index": [
            {
                "remote_path": r"C:\worker\evidence\readback.json",
                "relative_path": "readback.json",
            }
        ],
        "incremental_data": [{"record_type": "operation", "record_id": "event-1"}],
    }
    transport.queue(
        "collect-results",
        {"success": True, "results": [result_item], "next_sequence": 1},
        {"success": True, "results": [result_item], "next_sequence": 1},
    )

    first = manager.collect_results("PC001")
    second = manager.collect_results("PC001")

    assert first["collected"] == 1
    assert first["next_cursor"] == 1
    assert first["state_counts"] == {"SUCCESS": 1}
    assert first["summary"]["item_state_counts"] == {"BLOCKED": 1, "SUCCESS": 1}
    assert first["summary"]["error_class_counts"] == {"ACCOUNT_REVIEW": 1}
    assert first["summary"]["business_result_status"] == "observed"
    assert first["summary"]["incremental_record_count"] == 1
    assert len(transport.downloads) == 1
    assert second["collected"] == 0
    assert second["duplicates"] == 1
    assert manager.get_task("task-1")["task"]["message"] == "页面已回读"

    stored = manager.list_results("PC001")
    assert len(stored["results"]) == 1
    assert stored["results"][0]["record_id"].startswith("result:PC001:")
    assert stored["results"][0]["data_status"] == "observed"
    assert stored["results"][0]["account_slot"] == "brand-a"
    assert stored["results"][0]["capability"] == "search-feeds"
    assert stored["results"][0]["source_type"] == "manager"
    assert stored["results"][0]["task_request"]["request_summary"] == "搜索指定关键词"
    assert stored["results"][0]["business_result"]["count"] == 1
    assert stored["summary"]["item_results_status"] == "observed"


def test_collect_offline_preserves_previous_cursor(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.store.set_collection_cursor("PC001", 7)
    transport.queue("collect-results", ManagerTransportError("OFFLINE", "离线"))

    result = manager.collect_results("PC001")

    assert result["status"] == "OFFLINE"
    assert result["cursor"] == 7
    assert manager.store.get_collection_cursor("PC001") == 7


def test_incremental_records_deduplicate_natural_key_across_result_revisions(
    tmp_path: Path,
) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    common = {
        "task_id": "task-data",
        "remote_execution_id": "execution-data",
        "state": "SUCCESS",
        "item_results_status": "missing",
        "item_results_missing_reason": "本任务没有逐项结果",
        "incremental_data_status": "observed",
        "incremental_data": [{"record_type": "operation", "record_id": "event-1", "value": 1}],
    }
    transport.queue(
        "collect-results",
        {
            "success": True,
            "results": [
                {**common, "sequence": 1, "result_revision": 1},
                {**common, "sequence": 2, "result_revision": 2},
            ],
            "next_sequence": 2,
        },
    )

    result = manager.collect_results("PC001", download_evidence=False)

    assert result["collected"] == 2
    assert result["incremental_collected"] == 1
    assert result["incremental_duplicates"] == 1
    stored = manager.list_results("PC001")
    assert stored["incremental_unique_count"] == 1
    assert stored["incremental_records"][0]["record_id"] == "event-1"


def test_evidence_download_retries_after_structured_cursor_advances(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    result_item = {
        "sequence": 1,
        "result_revision": 1,
        "task_id": "task-evidence",
        "remote_execution_id": "execution-evidence",
        "state": "SUCCESS",
        "item_results": [],
        "item_results_status": "observed",
        "incremental_data": [],
        "incremental_data_status": "observed",
        "evidence_index": [
            {
                "remote_path": r"C:\worker\evidence\readback.json",
                "relative_path": "readback.json",
            }
        ],
    }
    transport.queue(
        "collect-results",
        {"success": True, "results": [result_item], "next_sequence": 1},
        {"success": True, "results": [], "next_sequence": 1},
    )
    transport.queue_download(ManagerTransportError("OFFLINE", "证据下载离线"), None)

    first = manager.collect_results("PC001")
    second = manager.collect_results("PC001")

    assert first["next_cursor"] == 1
    assert first["pending_evidence"] == 1
    assert first["status"] == "PARTIAL_SUCCESS"
    assert second["next_cursor"] == 1
    assert second["pending_evidence"] == 0
    assert second["status"] == "SUCCESS"
    assert len(transport.downloads) == 2


def test_collect_remote_error_preserves_previous_cursor(tmp_path: Path) -> None:
    manager, transport = service(tmp_path)
    register_ready(manager)
    manager.store.set_collection_cursor("PC001", 4)
    transport.queue(
        "collect-results",
        {
            "success": False,
            "error": {"code": "STATE_UNREADABLE", "message": "结果状态无法读取"},
        },
    )

    result = manager.collect_results("PC001")

    assert result["status"] == "BLOCKED"
    assert result["error_code"] == "STATE_UNREADABLE"
    assert manager.store.get_collection_cursor("PC001") == 4


def test_evidence_relative_path_stays_inside_revision_directory(tmp_path: Path) -> None:
    root = tmp_path / "revision"
    parts = ManagerService._safe_evidence_parts(
        r"C:\..\..\outside\readback.json",
        fallback="evidence-1",
    )

    target = root.joinpath(*parts).resolve()

    assert target.is_relative_to(root.resolve())
    assert ".." not in parts


def test_result_summary_does_not_turn_missing_data_into_observed_zero() -> None:
    summary = ManagerService._summarize_results(
        [
            {
                "task_id": "missing",
                "state": "SUCCESS",
                "item_results": [],
                "item_results_status": "missing",
                "item_results_missing_reason": "平台没有提供逐项数据",
                "business_result": {},
                "business_result_status": "missing",
                "business_result_missing_reason": "平台没有提供业务结果",
                "incremental_data": [],
                "incremental_data_status": "missing",
                "incremental_data_missing_reason": "本能力没有增量数据源",
            },
            {
                "task_id": "observed-empty",
                "state": "SUCCESS",
                "item_results": [],
                "item_results_status": "observed",
                "business_result": {},
                "business_result_status": "observed",
                "incremental_data": [],
                "incremental_data_status": "observed",
            },
        ]
    )

    assert summary["item_results_status"] == "mixed"
    assert summary["item_results_data_status_counts"] == {"missing": 1, "observed": 1}
    assert summary["business_result_status"] == "mixed"
    assert summary["incremental_data_status"] == "mixed"
    assert summary["incremental_record_count"] == 0
    assert {gap["task_id"] for gap in summary["data_gaps"]} == {"missing"}


def test_result_summary_does_not_assign_states_to_discovery_rows() -> None:
    feeds = [{"id": "feed-1", "displayTitle": "露营🏕️"}]

    summary = ManagerService._summarize_results(
        [
            {
                "task_id": "search",
                "state": "SUCCESS",
                "business_result": {"feeds": feeds, "count": 1},
                "business_result_status": "observed",
                "item_results": feeds,
                "item_results_status": "observed",
                "incremental_data": [],
                "incremental_data_status": "observed",
            }
        ]
    )

    assert summary["item_results_status"] == "observed"
    assert summary["item_state_counts"] == {}
    assert summary["data_gaps"] == []
