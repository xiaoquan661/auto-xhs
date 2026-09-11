from __future__ import annotations

import json
from pathlib import Path

import pytest
from employee_operations import PLAN_METRICS, EmployeeOperations, flatten_observations
from manager_cli import main
from manager_service import ManagerService
from manager_store import ManagerStore
from manager_worker_service import ManagerWorkerService
from service_errors import ServiceError
from web_server import _dispatch_api, _dispatch_mutation


def manager_at(root: Path) -> ManagerService:
    manager = ManagerService(store=ManagerStore(root))
    for machine in ("PC001", "PC002"):
        manager.store.put("machines", machine, {"machine_id": machine})
        manager.store.put(
            "accounts",
            f"{machine}:alpha",
            {
                "machine_id": machine,
                "account_slot": "alpha",
                "account_key": f"{machine}:alpha",
            },
        )
    return manager


def view(manager: ManagerService) -> EmployeeOperations:
    return EmployeeOperations(
        manager.store.snapshot(),
        manager.list_results()["current_results"],
        manager._result_metric_counts,
    )


def test_private_message_is_separate_from_reply_and_inbound(tmp_path):
    from manager_store import ManagerStore

    manager = ManagerService(store=ManagerStore(tmp_path))
    request = {"capability": "send-private-messages", "account_slot": "alpha",
               "parameters": {"count": 1}}
    manager.store.put("tasks", "dm", {"task_id": "dm", "machine_id": "PC001",
                                      "created_at": "2026-09-08T02:00:00Z", "request": request})
    receipt(manager, "dm", capability="send-private-messages", task_request=request,
            item_results=[{"success": True, "platform_readback": True}])
    metrics = view(manager).day_metrics("PC001", "alpha", "2026-09-08")
    assert metrics["private_message"]["target"] == 1
    assert metrics["private_message"]["actual"] == 1
    assert metrics["reply"]["actual"] is None
    assert metrics["received_private_messages"]["actual"] is None
    assert view(manager).day_metrics("PC002", "alpha", "2026-09-08")[
        "private_message"]["target"] is None


@pytest.mark.parametrize("state,label", [
    ("RESULT_UNKNOWN", "执行结果待核对"), ("BLOCKED", "已阻塞"), ("FAILED", "执行失败"),
])
def test_daily_metric_explains_terminal_execution(tmp_path, state, label):
    manager = manager_at(tmp_path)
    receipt(manager, "reply", capability="reply-inbox", state=state,
            task_request={"capability": "reply-inbox", "account_slot": "alpha",
                          "parameters": {"acceptance_action": "reply"}})
    metrics = view(manager).day_metrics("PC001", "alpha", "2026-09-08")
    assert metrics["reply"]["status"] == label
    assert metrics["reply"]["incomplete"] is False


def test_complete_worker_event_enriches_summary_with_account_isolation(tmp_path):
    from manager_store import ManagerStore

    manager = ManagerService(store=ManagerStore(tmp_path))
    state = manager.store.snapshot()
    summary = {"event_id": "e", "platform_event_id": "p", "event_type": "note_comment",
               "summary": "历史通知首次入库，按规则跳过", "historical_backfill": True}
    state["incremental_records"] = {"r": {"machine_id": "PC001", "account_slot": "alpha",
                                            "data": summary}}
    full = {**summary, "handling_state": "IGNORED", "occurred_at": "2026-09-01T00:00:00Z",
            "payload": {"nickname": "阿璇日记", "content": "具体评论", "feed_id": "note",
                        "comment_id": "comment"}}
    state["worker_observations"] = [{"machine_id": "PC001", "account_slot": "alpha",
                                     "data": full},
                                    {"machine_id": "PC002", "account_slot": "alpha",
                                     "data": {**full, "payload": {"nickname": "其他账号"}}}]
    operations = EmployeeOperations(state, [], manager._result_metric_counts)
    rows = operations.observations["PC001", "alpha"]
    assert len(rows) == 1
    item = operations.event_view(rows[0])
    assert item["actor_name"] == "阿璇日记"
    assert item["reply_text"] == "具体评论"
    assert item["handling_state"] == "IGNORED" and not item["requires_reply"]
    assert item["target_url"].endswith("/explore/note")
    assert item["historical_backfill"] is True


def receipt(manager: ManagerService, identifier: str, **fields: object) -> dict:
    result = {
        "machine_id": "PC001",
        "account_slot": "alpha",
        "task_id": identifier,
        "remote_execution_id": identifier,
        "result_revision": 1,
        "state": "SUCCESS",
        "recorded_at": "2026-09-08T02:00:00Z",
        **fields,
    }
    manager.store.put("results", f"{identifier}:{result['result_revision']}", result)
    return result


def event(identifier: str, kind: str = "note_comment", **fields: object) -> dict:
    return {
        "event_id": identifier,
        "platform_event_id": identifier,
        "event_type": kind,
        "account_slot": "alpha",
        "actor_user_id": "reader",
        "object_id": "note-1",
        "handling_state": "NEW",
        "occurred_at": "2026-09-07T16:30:00Z",
        "payload": {
            "nickname": "读者",
            "content": "很有帮助",
            "feed_id": "note-1",
            "parent_comment_content": "原评论",
        },
        **fields,
    }


def test_collectors_roundtrip_nested_events_and_metrics() -> None:
    snapshots = {
        "account": {
            "created": True,
            "snapshot": {"entity_type": "account", "snapshot_id": "s1", "followers": 8},
        },
        "notes": [{"created": True, "snapshot": {"entity_type": "note", "snapshot_id": "s2"}}],
    }
    output = ManagerWorkerService._normalize_outcome(
        {"state": "SUCCESS", "result": {"snapshots": snapshots}}
    )
    assert [r["snapshot_id"] for r in output["incremental_data"]] == ["s1", "s2"]
    output = ManagerWorkerService._normalize_outcome(
        {
            "state": "SUCCESS",
            "result": {
                "inbound_events": {
                    "created_events": [event("e1")],
                    "existing_events": [event("e2")],
                    "human_owned_events": [event("e3", "private_message")],
                }
            },
        }
    )
    assert [r["event_id"] for r in output["incremental_data"]] == ["e1", "e2", "e3"]
    assert flatten_observations({"created_events": [], "observed_count": 0}) == []


def test_shared_plan_preserves_extended_fields_from_other_editors(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    row = {
        "machine_id": "PC001",
        "account_slot": "alpha",
        "participating": True,
        "stage": "稳定运营",
        "pressure": "高压",
        "metrics": {
            "new_followers": 5,
            "friend_request": 2,
            "publish": 1,
            "received_comments": None,
        },
    }
    body = {"plan_date": "2026-09-08", "accounts": [row]}
    _dispatch_mutation(None, "POST", "/api/v1/manager/daily-plans", body, manager_service=manager)
    body["accounts"] = [
        {
            "machine_id": "PC001",
            "account_slot": "alpha",
            "participating": True,
            "metrics": {"like": 4},
        }
    ]
    saved = manager.save_daily_plan(body)["plan"]
    assert saved["accounts"][0]["pressure"] == "高压"
    assert saved["accounts"][0]["metrics"]["new_followers"] == 5
    assert saved["accounts"][0]["metrics"]["like"] == 4
    assert set(saved["accounts"][0]["metrics"]) == set(PLAN_METRICS)
    assert saved["accounts"][0]["metrics"]["received_comments"] is None
    assert manager.store.list("tasks") == []
    assert ManagerStore(tmp_path).list("daily_plans")[0] == saved


def test_account_configuration_route_persists_and_drives_summary(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    path = "/api/v1/manager/machines/PC001/accounts/alpha/operations"
    _dispatch_mutation(
        None,
        "PATCH",
        path,
        {
            "stage": "稳定运营",
            "pressure": "低压",
            "operating_since": "2026-09-01",
            "publish_audience": "摄影爱好者",
            "comment_range": "3–5",
            "publish_rules": {"daily_limit": 2, "min_interval_minutes": 180},
            "exception_policy": "出现验证时暂停并通知经理",
        },
        manager_service=manager,
    )
    _dispatch_mutation(None, "PATCH", path, {"stage": "账号安全观察"}, manager_service=manager)
    reopened = ManagerService(store=ManagerStore(tmp_path))
    account = view(reopened).summary(reopened.list_accounts()["accounts"], "2026-09-08")[
        "accounts"
    ][0]
    assert account["operating_days"] == 8
    assert account["stage"] == "账号安全观察"
    assert account["publish_rules"]["daily_limit"] == 2
    assert account["publish_audience"] == "摄影爱好者"
    assert account["planning_evidence"]["comment_range"] == "3–5"
    assert _dispatch_api(None, "/api/v1/manager/employee-summary", manager_service=manager)[
        "success"
    ]
    assert manager.store.list("tasks") == []


def test_event_replays_account_isolation_and_china_calendar(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    batch = {"created_events": [event("same"), event("pm", "private_message")]}
    first = receipt(
        manager, "collect", capability="collect-note-comments", incremental_data=[batch]
    )
    manager._record_incremental_data("PC001", first)
    receipt(
        manager, "collect-again", incremental_data=[event("same"), event("pm", "private_message")]
    )
    receipt(manager, "other", machine_id="PC002", incremental_data=[event("same")])
    summary = view(manager).summary(manager.list_accounts()["accounts"], "2026-09-08")
    one, two = summary["accounts"]
    assert len(one["comment_reply_alerts"]) == len(two["comment_reply_alerts"]) == 1
    assert one["comment_reply_alerts"][0]["parent_comment_text"] == "原评论"
    assert one["comment_reply_alerts"][0]["requires_reply"]
    assert one["session_count"] == 1
    assert two["private_message_events"] == []
    metrics = view(manager).day_metrics("PC001", "alpha", "2026-09-08")
    assert metrics["received_comments"]["actual"] is None
    assert metrics["received_comments"]["observed_new"] == 1
    assert metrics["received_private_messages"]["observed_new"] == 1
    assert metrics["received_comments"]["incomplete"]
    assert metrics["received_comments"]["observation"]["source"] == "platform_event_time"
    assert (
        view(manager).day_metrics("PC001", "alpha", "2026-09-07")["received_comments"]["actual"]
        is None
    )


def test_daily_growth_uses_adjacent_snapshot_dates_and_same_identity(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    samples = [
        {
            "entity_type": "account",
            "snapshot_id": f"s{i}",
            "entity_id": "uid-1",
            "captured_at": f"2026-09-{d}T12:00:00+08:00",
            "followers": followers,
            "likes": likes,
            "notes": 8,
        }
        for i, (d, followers, likes) in enumerate((("07", 20, 30), ("08", 18, 40)))
    ]
    receipt(manager, "metrics", incremental_data=samples)
    metrics = view(manager).day_metrics("PC001", "alpha", "2026-09-08")
    assert metrics["new_followers"]["actual"] is None
    assert metrics["received_likes"]["actual"] is None
    assert metrics["new_followers"]["sampled_net_change"]["value"] == -2
    assert metrics["received_likes"]["sampled_net_change"]["value"] == 10
    assert metrics["new_followers"]["current_total"] == 18
    assert (
        view(manager).day_metrics("PC001", "alpha", "2026-09-09")["new_followers"]["actual"] is None
    )
    receipt(
        manager,
        "changed",
        incremental_data=[
            {
                **samples[1],
                "entity_id": "uid-2",
                "snapshot_id": "s3",
                "captured_at": "2026-09-09T12:00:00+08:00",
            }
        ],
    )
    assert (
        view(manager).day_metrics("PC001", "alpha", "2026-09-09")["new_followers"]["actual"] is None
    )


def test_follow_and_keyword_receipts_count_changes_only(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    for i, changed in enumerate((True, False)):
        receipt(
            manager,
            f"follow-{i}",
            capability="follow-user",
            business_result={
                "success": True,
                "changed": changed,
                "readback": {"user_id": f"u{i}", "nickname": "读者"},
            },
        )
    receipt(
        manager,
        "likes",
        capability="keyword-engagement",
        item_results=[
            {"actions": {"like": {"status": "success"}}},
            {"actions": {"like": {"status": "skipped"}}},
        ],
    )
    metrics = view(manager).day_metrics("PC001", "alpha", "2026-09-08")
    assert metrics["friend_request"]["actual"] == 1
    assert metrics["like"]["actual"] == 1
    assert (
        len(
            view(manager).summary(manager.list_accounts()["accounts"], "2026-09-08")["accounts"][0][
                "friend_events"
            ]
        )
        == 2
    )


def test_report_uses_latest_revision_and_reports_missing_group(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    manager.save_daily_plan(
        {
            "plan_date": "2026-09-08",
            "accounts": [
                {
                    "machine_id": machine,
                    "account_slot": "alpha",
                    "participating": True,
                    "pressure": pressure,
                    "metrics": {"comment": 2, "publish": 0},
                }
                for machine, pressure in (("PC001", "高压"), ("PC002", "低压"))
            ],
        }
    )
    receipt(manager, "comment", capability="post-comment", actual_count=1)
    receipt(manager, "comment", capability="post-comment", actual_count=2, result_revision=2)
    report = view(manager).report("2026-09-08")
    assert report["rows"][0]["actual"] == 2
    assert report["rows"][0]["completion_rate"] == 1
    assert report["groups"][1]["completion_rate"] is None
    assert report["conclusion"] == "等待两组完整回执"
    assert report["rows"][1]["metrics"]["comment"]["actual"] is None
    receipt(
        manager,
        "low",
        machine_id="PC002",
        capability="post-comment",
        actual_count=1,
        state="FAILED",
        execution_source="human",
    )
    report = view(manager).report("2026-09-08")
    assert report["exception_count"] == 1
    assert report["rows"][1]["external_actions"] == 1
    assert "低压组 50%" in report["conclusion"]


@pytest.mark.parametrize(
    "body", [{"operating_since": "bad"}, {"publish_rules": {"daily_limit": -1}}]
)
def test_invalid_config_does_not_overwrite_existing(tmp_path: Path, body: dict) -> None:
    manager = manager_at(tmp_path)
    manager.save_account_operations("PC001", "alpha", {"stage": "稳定运营"})
    with pytest.raises(ServiceError):
        manager.save_account_operations("PC001", "alpha", body)
    assert manager.store.get("accounts", "PC001:alpha")["operations_config"]["stage"] == "稳定运营"


def test_cli_configuration_and_summary_use_shared_manager_store(tmp_path: Path, capsys) -> None:
    manager = manager_at(tmp_path)
    config = tmp_path / "operations.json"
    config.write_text(json.dumps({"stage": "稳定运营"}), encoding="utf-8")
    assert (
        main(
            [
                "employee",
                "configure",
                "--machine-id",
                "PC001",
                "--account",
                "alpha",
                "--input-file",
                str(config),
            ],
            service_factory=lambda _: manager,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out)["operations_config"]["stage"] == "稳定运营"
    assert main(["employee", "summary"], service_factory=lambda _: manager) == 0
    assert json.loads(capsys.readouterr().out)["accounts"][0]["stage"] == "稳定运营"


def test_publishing_counts_verified_completion_and_home_counts_observed_zero(
    tmp_path: Path,
) -> None:
    manager = manager_at(tmp_path)
    receipt(
        manager,
        "draft",
        capability="fill-publish",
        business_result={"verified": True, "evidence": "draft_success_toast"},
    )
    receipt(
        manager,
        "published",
        capability="fill-publish",
        business_result={"verified": True, "evidence": "note_manager_readback"},
    )
    receipt(
        manager,
        "browse",
        capability="home-engagement",
        item_results_status="observed",
        item_results=[{"browse": {"status": "success"}, "like": {"status": "skipped"}}],
    )
    metrics = view(manager).day_metrics("PC001", "alpha", "2026-09-08")
    assert metrics["publish"]["actual"] == 1
    assert metrics["browse"]["actual"] == 1
    assert metrics["like"]["actual"] == 0


def test_action_attribution_updates_summary_and_preserves_original_receipt(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    original = receipt(
        manager,
        "comments",
        record_id="result:PC001:comments:1",
        capability="random-comment",
        item_results_status="observed",
        item_results=[{"status": "SUCCESS"}, {"status": "SUCCESS"}],
    )
    path = "/api/v1/manager/results/result%3APC001%3Acomments%3A1/attribution"
    _dispatch_mutation(
        None, "PATCH", path, {"item_index": 1, "execution_source": "human"}, manager_service=manager
    )
    reopened = ManagerService(store=ManagerStore(tmp_path))
    results = reopened.list_results()
    current = results["current_results"][0]
    assert current["item_results"][1]["execution_source"] == "human"
    assert view(reopened).external_action_count(current) == 1
    assert results["results"][0] == original
    assert "execution_source" not in results["result_history"][0]["item_results"][1]
    assert manager.store.list("tasks") == []
    _dispatch_mutation(
        None,
        "PATCH",
        path,
        {"item_index": 1, "execution_source": "machine"},
        manager_service=manager,
    )
    assert manager.store.list("action_attributions")[0]["previous_source"] == "human"
    assert view(manager).external_action_count(manager.list_results()["current_results"][0]) == 0


def test_action_attribution_validates_target_item(tmp_path: Path) -> None:
    manager = manager_at(tmp_path)
    receipt(manager, "single", record_id="single", capability="post-comment")
    with pytest.raises(ServiceError, match="有效的动作"):
        manager.save_action_attribution("single", {"item_index": 1, "execution_source": "human"})
    assert manager.store.list("action_attributions") == []
