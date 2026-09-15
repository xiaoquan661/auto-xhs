from __future__ import annotations

from datetime import UTC, datetime

import pytest
from application_service import ApplicationService
from business_runner import BusinessRunner
from employee_operations import EmployeeOperations
from manager_agent_service import ManagerAgentService
from operations_context import event_location, warm_candidates
from operations_policy import check_operations_policy
from product_store import ProductStore
from service_errors import ServiceError
from xhs.growth_notifications import normalize_growth_notifications

from tests.test_manager_agent_service import build_manager


def test_daily_plan_prepare_is_idempotent_and_preserves_each_account_target(tmp_path, monkeypatch):
    manager, transport = build_manager(tmp_path)
    manager.store.put(
        "accounts",
        "PC057:tesssst",
        {
            "machine_id": "PC057",
            "account_slot": "tesssst",
            "platform": "rednote",
            "health_state": "READY",
            "current_uid": "owner",
        },
    )
    monkeypatch.setattr(
        manager, "list_accounts", lambda: {"accounts": manager.store.list("accounts")}
    )
    manager.store.put(
        "incremental_records",
        "warm",
        {
            "machine_id": "PC057",
            "account_slot": "tesssst",
            "data": [
                {
                    "event_type": "note_comment",
                    "event_id": "e1",
                    "actor_user_id": "reader",
                    "occurred_at": "2026-09-08T10:00:00+08:00",
                    "payload": {"content": "留言"},
                }
            ],
        },
    )
    manager.save_daily_plan(
        {
            "plan_date": "2026-09-09",
            "accounts": [
                {
                    "machine_id": "PC057",
                    "account_slot": "tesssst",
                    "participating": True,
                    "metrics": {"publish": 1, "reply": 1, "friend_request": 1, "new_followers": 2},
                }
            ],
        }
    )
    inputs = {
        "action_inputs": {
            "PC057:tesssst": {
                "publish": [
                    {"title": "标题", "content": "正文", "assets": ["C:/assets/photo.jpg"]}
                ],
                "reply": [{"content": "回复", "feed_id": "note", "comment_id": "comment"}],
                "friend_request": [{"user_id": "reader", "xsec_token": "target-context"}],
            }
        }
    }
    first = manager.prepare_daily_plan("2026-09-09", inputs)
    second = manager.prepare_daily_plan("2026-09-09", inputs)
    task = first["operation_tasks"][0]
    assert task["operation_task_id"] == second["operation_tasks"][0]["operation_task_id"]
    planner_input = {**task, "goal_snapshot": {"target_metrics": task["daily_material"]["budgets"]}}
    plan = ManagerAgentService(manager)._build_plan(planner_input)
    assert plan["unsupported_metrics"] == []
    assert {a["capability"] for a in plan["planned_actions"]} == {
        "fill-publish",
        "reply-comment",
        "follow-user",
        "collect-operations-metrics",
        "collect-note-comments",
        "collect-private-messages",
    }
    request = ManagerAgentService(manager)._task_request(task, plan["planned_actions"][0])
    assert request["platform"] == "rednote"
    assert transport.accepted == []


def test_plan_reports_missing_publish_material_without_dispatch(tmp_path):
    manager, _ = build_manager(tmp_path)
    plan = ManagerAgentService(manager)._build_plan(
        {
            "goal_snapshot": {"target_metrics": {"publish": 1}},
            "targets": [{"machine_id": "PC057", "account_slot": "tesssst"}],
        }
    )
    assert "素材" in plan["unsupported_metrics"][0]
    assert not plan["planned_actions"]


@pytest.mark.parametrize(
    ("config", "history", "code"),
    [
        ({"publish_rules": {"daily_limit": 0}}, [], "OPERATIONS_PUBLISH_LIMIT"),
        (
            {"publish_rules": {"min_interval_minutes": 60}},
            [
                {
                    "capability": "fill-publish",
                    "state": "SUCCESS",
                    "completed_at": "2026-09-09T01:30:00Z",
                    "business_result": {"verified": True, "evidence": "success_page"},
                }
            ],
            "OPERATIONS_PUBLISH_WAIT",
        ),
        (
            {"publish_rules": {"daily_limit": 5}},
            [
                {
                    "capability": "fill-publish",
                    "state": "RESULT_UNKNOWN",
                }
            ],
            "OPERATIONS_PUBLISH_UNKNOWN",
        ),
        ({"time_windows": [{"start": "14:00", "end": "17:00"}]}, [], "OPERATIONS_WINDOW_WAIT"),
    ],
)
def test_policy_stops_disallowed_publish(config, history, code):
    with pytest.raises(ServiceError) as error:
        check_operations_policy(
            config, "fill-publish", history, now=datetime(2026, 9, 9, 2, tzinfo=UTC)
        )
    assert error.value.code == code


def test_policy_allows_reads_and_uses_account_exception_mapping():
    config = {"exception_rules": {"PHONE_VERIFICATION": "pause"}}
    check_operations_policy(
        config,
        "collect-operations-metrics",
        [],
        account_state="PHONE_VERIFICATION",
        write_intent=False,
    )
    with pytest.raises(ServiceError, match="暂停"):
        check_operations_policy(config, "reply-comment", [], account_state="PHONE_VERIFICATION")


def test_warm_history_spans_days_and_excludes_already_followed():
    events = [
        {
            "event_id": f"e{i}",
            "actor_user_id": user,
            "event_type": "note_comment",
            "occurred_at": f"2026-09-0{i + 1}T12:00:00Z",
            "platform": "rednote",
            "payload": {"content": "留言", "nickname": user},
        }
        for i, user in enumerate(["a", "b", "a"])
    ]
    results = [
        {
            "capability": "follow-user",
            "business_result": {"success": True, "readback": {"user_id": "b"}},
        }
    ]
    candidates = warm_candidates(events, results)
    assert [r["user_id"] for r in candidates] == ["a"]
    assert len(candidates[0]["evidence"]) == 2
    assert "rednote.com" in candidates[0]["profile_url"]


def test_navigation_preserves_observed_comment_link_and_separates_read_status():
    event = {
        "event_type": "comment_reply",
        "platform": "rednote",
        "object_id": "note",
        "payload": {"comment_id": "c", "content": "原内容", "nickname": "读者"},
    }
    location = event_location(event)
    assert location["target_url"] == "https://www.rednote.com/explore/note"
    assert location["comment_id"] == "c" and location["location_scope"] == "note"
    event["payload"].update(
        target_url="https://www.rednote.com/explore/note?commentId=c", location_scope="comment"
    )
    assert event_location(event)["target_url"].endswith("?commentId=c")
    pm = EmployeeOperations.event_view(
        {
            "event_type": "private_message",
            "platform": "rednote",
            "actor_user_id": "reader",
            "handling_state": "HANDLED",
            "payload": {"read_status": "UNREAD"},
        }
    )
    assert pm["thread_url"] == "https://www.rednote.com/chat/reader"
    assert pm["platform_read_status"] == "UNREAD"
    assert pm["handling_state"] == "HANDLED"


def test_growth_events_deduplicate_and_do_not_invent_platform_time(tmp_path):
    raw = {"event_type": "new_follower", "user_id": "reader", "notification_id": "n1"}
    events = normalize_growth_notifications([raw, raw], "2026-09-09T02:00:00Z")
    assert len(events) == 1 and events[0]["time_scope"] == "first_observed"
    app = ApplicationService(product_store=ProductStore(tmp_path / "product"))
    result = {
        "captured_at": "2026-09-09T02:00:00Z",
        "account": {"entity_id": "owner", "metrics": {"followers": 3}},
        "growth_notifications": {"events": events, "coverage": "visible_notifications"},
    }
    first = app._record_metric_collection("alpha", result, platform="rednote")
    second = app._record_metric_collection("alpha", result, platform="rednote")
    assert first["events"][0]["event_id"] == second["events"][0]["event_id"]
    assert second["events"][0]["platform"] == "rednote"


def test_manager_publish_records_unknown_when_readback_is_missing(tmp_path, monkeypatch):
    calls = []
    app = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(
            lambda account, cap, params: calls.append(cap) or {"success": True, "published": False}
        ),
    )
    monkeypatch.setattr(app, "_resolve_account_platform", lambda _: "xiaohongshu")
    monkeypatch.setattr(
        app, "get_account_status", lambda _: {"ready": True, "identity": {"live_user_id": "owner"}}
    )
    result = app.create_task(
        source="manager",
        source_type="manager-agent",
        account_slot="alpha",
        capability="fill-publish",
        request_summary="准备发布",
        parameters={
            "preview": {
                "kind": "image",
                "title": "标题",
                "content": "正文",
                "assets": ["C:/photo.jpg"],
            }
        },
    )
    task = app.execute_task(result["task"]["task_id"])
    assert task["task"]["state"] == "RESULT_UNKNOWN"
    assert calls == ["fill-publish", "click-publish"]
    with pytest.raises(ServiceError) as error:
        app.execute_task(task["task"]["task_id"])
    assert error.value.code == "INVALID_TASK_STATE"


def test_employee_checks_publish_policy_before_browser_action(tmp_path, monkeypatch):
    calls = []
    app = ApplicationService(
        product_store=ProductStore(tmp_path / "product"),
        business_runner=BusinessRunner(lambda *args: calls.append(args) or {}),
    )
    monkeypatch.setattr(app, "_resolve_account_platform", lambda _: "xiaohongshu")
    monkeypatch.setattr(
        app, "get_account_status", lambda _: {"ready": True, "identity": {"live_user_id": "owner"}}
    )
    created = app.create_task(
        source="manager",
        source_type="manager-agent",
        account_slot="alpha",
        capability="fill-publish",
        request_summary="准备发布",
        parameters={
            "operations_config": {"publish_rules": {"daily_limit": 0}},
            "preview": {
                "kind": "image",
                "title": "标题",
                "content": "正文",
                "assets": ["C:/photo.jpg"],
            },
        },
    )
    result = app.execute_task(created["task"]["task_id"])
    assert result["task"]["error_code"] == "OPERATIONS_PUBLISH_LIMIT"
    assert not calls


def test_history_collection_keeps_observed_publish_date_and_scope():
    from xhs.metrics_collector import collect_operations_metrics
    from xhs.types import Feed, FeedDetail, FeedDetailResponse, UserProfileResponse

    profile = UserProfileResponse(feeds=[Feed(id="old-note")])
    result = collect_operations_metrics(
        object(),
        owner_user_id="owner",
        profile_loader=lambda *args: profile,
        note_loader=lambda *args: FeedDetailResponse(note=FeedDetail(time=1788919200000)),
    )
    assert result["notes"][0]["extra"]["published_at"] == 1788919200000
    assert result["history_coverage"] == "profile_sample"


def test_manager_reads_historical_post_dates_and_cross_day_interactions(tmp_path):
    from tests.test_employee_operations import event, manager_at, receipt, view

    manager = manager_at(tmp_path)
    previous = event("past", occurred_at="2026-09-01T01:00:00Z")
    receipt(
        manager,
        "history",
        capability="collect-operations-metrics",
        incremental_data=[
            previous,
            {
                "entity_type": "note",
                "entity_id": "old",
                "snapshot_id": "n1",
                "extra": {"published_at": "2026-08-15T08:00:00+08:00"},
            },
        ],
    )
    account = view(manager).enrich_account(manager.list_accounts()["accounts"][0], "2026-09-09")
    assert account["planning_evidence"]["last_post_date"] == "2026-08-15"
    assert account["planning_evidence"]["warm_candidate_count"] == 1


def test_manager_policy_blocks_before_ssh(tmp_path):
    manager, transport = build_manager(tmp_path)
    manager.store.put(
        "accounts",
        "PC057:tesssst",
        {
            "machine_id": "PC057",
            "account_slot": "tesssst",
            "health_state": "READY",
            "operations_config": {"publish_rules": {"daily_limit": 0}},
        },
    )
    request = {
        "task_id": "blocked-publish",
        "machine_id": "PC057",
        "account_slot": "tesssst",
        "capability": "fill-publish",
        "write_intent": True,
        "parameters": {},
    }
    with pytest.raises(ServiceError) as error:
        manager.dispatch_task(request)
    assert error.value.code == "OPERATIONS_PUBLISH_LIMIT"
    assert not transport.accepted


def test_verified_draft_does_not_consume_publishing_limit():
    check_operations_policy(
        {"publish_rules": {"daily_limit": 1}},
        "fill-publish",
        [
            {
                "capability": "fill-publish",
                "state": "SUCCESS",
                "completed_at": "2026-09-09T01:00:00Z",
                "business_result": {"verified": True, "evidence": "draft_success_toast"},
            }
        ],
        now=datetime(2026, 9, 9, 2, tzinfo=UTC),
    )


def test_growth_target_uses_collected_events_instead_of_collector_action_count(tmp_path):
    from tests.test_employee_operations import event, manager_at, receipt

    manager = manager_at(tmp_path)
    receipt(
        manager,
        "observed",
        capability="collect-note-comments",
        actual_count=99,
        incremental_data=[event("incoming")],
    )
    goal = {
        "period_start": "2026-09-08",
        "period_end": "2026-09-08",
        "target_metrics": {"received_comments": 1},
    }
    counts = manager._observed_growth_metrics(
        goal, [{"machine_id": "PC001", "account_slot": "alpha"}]
    )
    assert counts == {"received_comments": 1}


def test_cli_publish_confirmation_rechecks_account_policy(tmp_path):
    from publish_workflow import PublishWorkflowService

    store = ProductStore(tmp_path / "product")
    workflow = PublishWorkflowService(store)
    task = workflow.begin(
        account="alpha",
        capability="fill-publish",
        request_summary="图文",
        preview={"kind": "image", "title": "标题", "content": "正文", "assets": ["C:/photo.jpg"]},
    )
    waiting = workflow.wait_for_confirmation(task["task_id"], summary="预览已准备")
    store.set_setting("operations_config:alpha", {"publish_rules": {"daily_limit": 0}})
    with pytest.raises(ServiceError) as error:
        workflow.prepare_publish(
            task["task_id"],
            account="alpha",
            confirmed=True,
            preview_revision=waiting["parameters"]["preview_revision"],
        )
    assert error.value.code == "OPERATIONS_PUBLISH_LIMIT"
    assert workflow.tasks.get(task["task_id"])["state"] == "RUNNING"


def test_saving_draft_remains_available_when_publish_limit_is_reached(tmp_path):
    from publish_workflow import PublishWorkflowService

    store = ProductStore(tmp_path / "product")
    workflow = PublishWorkflowService(store)
    task = workflow.begin(
        account="alpha",
        capability="fill-publish",
        request_summary="图文",
        preview={"kind": "image", "title": "标题", "content": "正文", "assets": ["C:/photo.jpg"]},
    )
    waiting = workflow.wait_for_confirmation(task["task_id"], summary="预览已准备")
    store.set_setting("operations_config:alpha", {"publish_rules": {"daily_limit": 0}})
    result = workflow.prepare_publish(
        task["task_id"],
        account="alpha",
        confirmed=True,
        preview_revision=waiting["parameters"]["preview_revision"],
        action="save-draft",
    )
    assert result["state"] == "RUNNING"


def test_growth_visible_dates_and_repeated_collection_keep_stable_event_identity():
    rows = [{"event_type": "new_follower", "user_id": "reader", "display_time": "昨天 21:00"}]
    first = normalize_growth_notifications(rows, "2026-09-09T02:00:00Z")[0]
    later = normalize_growth_notifications(rows, "2026-09-09T05:00:00Z")[0]
    assert first["occurred_at"] == "2026-09-08T00:00:00+08:00"
    assert first["time_scope"] == "platform_display_date"
    assert first["platform_event_id"] == later["platform_event_id"]


def test_growth_collection_reads_both_notification_tabs():
    from xhs.growth_notifications import collect_growth_notifications

    class Page:
        def __init__(self):
            self.tab = 0

        def navigate(self, url):
            self.url = url

        def wait_for_load(self):
            pass

        def wait_dom_stable(self):
            pass

        def evaluate(self, script):
            if "const labels" in script:
                self.tab += 1
                return True
            return [
                {
                    "event_type": "received_like" if self.tab == 1 else "new_follower",
                    "user_id": "reader",
                    "notification_id": str(self.tab),
                }
            ]

    result = collect_growth_notifications(Page())
    assert {row["event_type"] for row in result["events"]} == {"received_like", "new_follower"}
    assert result["tabs_observed"] == [True, True]
    assert result["partial"] is True


def test_outbound_comment_receipt_supplies_warm_interaction_evidence():
    candidates = warm_candidates(
        [],
        [
            {
                "record_id": "comment-result",
                "capability": "random-comment",
                "platform": "rednote",
                "completed_at": "2026-09-08T01:00:00Z",
                "item_results": [{"success": True, "author_user_id": "reader", "content": "你好"}],
            }
        ],
    )
    assert candidates[0]["user_id"] == "reader"
    assert candidates[0]["profile_url"].startswith("https://www.rednote.com/")
    assert candidates[0]["evidence"][0]["event_id"] == "comment-result:0"


def test_private_collection_returns_thread_and_precollection_read_status():
    from xhs.private_inbox import collect_private_messages

    from tests.test_private_inbox import FakePage

    class Page(FakePage):
        def evaluate(self, script):
            result = super().evaluate(script)
            if ".xhs-im-conv-item" in script:
                result[0]["read_status"] = "UNREAD"
            return result

    message = collect_private_messages(Page())["messages"][0]
    assert message["thread_url"].endswith("/chat/user-1")
    assert message["read_status"] == "UNREAD"
    assert message["read_status_observed_at"]


def test_worker_recovers_final_publication_after_preview_confirmation(tmp_path, monkeypatch):
    from tests.test_manager_worker import make_service, task_request

    service = make_service(tmp_path / "worker")
    accepted = service.accept_task(task_request())["execution"]
    execution_id = accepted["remote_execution_id"]

    def mark_waiting(state):
        row = state["executions"][execution_id]
        row.update(
            state="BLOCKED",
            local_task_id="local-publish",
            error_code="PUBLISH_CONFIRMATION_REQUIRED",
        )
        service._append_result(state, row)

    service._store.mutate(mark_waiting)
    local_state = {"state": "WAITING_APPROVAL"}
    monkeypatch.setattr(
        service,
        "_read_local_task_outcome",
        lambda _: {
            "state": local_state["state"],
            "result": {"verified": True, "published": True, "evidence": "success_page"},
        },
    )
    waiting = service.task_status(remote_execution_id=execution_id)["execution"]
    assert ManagerAgentService._task_state(waiting) == "WAITING_APPROVAL"
    assert len(service.collect_results(0)["results"]) == 1
    local_state["state"] = "SUCCESS"
    completed = service.task_status(remote_execution_id=execution_id)["execution"]
    assert completed["state"] == "SUCCESS"
    assert completed["error_code"] == ""
    assert service.collect_results(0)["results"][-1]["business_result"]["published"] is True


def test_completed_local_publish_is_counted_by_later_publish_policy(tmp_path):
    from publish_workflow import PublishWorkflowService

    store = ProductStore(tmp_path / "product")
    workflow = PublishWorkflowService(store)
    preview = {"kind": "image", "title": "标题", "content": "正文", "assets": ["C:/photo.jpg"]}
    first = workflow.begin(
        account="alpha", capability="fill-publish", request_summary="图文", preview=preview
    )
    workflow.complete_publish(first["task_id"], {"verified": True, "evidence": "success_page"})
    second = workflow.begin(
        account="alpha", capability="fill-publish", request_summary="图文", preview=preview
    )
    waiting = workflow.wait_for_confirmation(second["task_id"], summary="预览已准备")
    store.set_setting("operations_config:alpha", {"publish_rules": {"daily_limit": 1}})
    with pytest.raises(ServiceError) as error:
        workflow.prepare_publish(
            second["task_id"],
            account="alpha",
            confirmed=True,
            preview_revision=waiting["parameters"]["preview_revision"],
        )
    assert error.value.code == "OPERATIONS_PUBLISH_LIMIT"
