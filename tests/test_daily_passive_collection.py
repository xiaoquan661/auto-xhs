from __future__ import annotations

import json
from types import SimpleNamespace

import daily_passive_collection as daily


def test_daily_channels_collect_followers_and_comments():
    from application_service import ApplicationService

    for command in daily.commands("xiaohongshu"):
        parameters = {command[1][2:].replace("-", "_"): command[2]}
        ApplicationService._validate_task_parameters(command[0], parameters)
    assert [c[0] for c in daily.commands("xiaohongshu")] == [
        "collect-operations-metrics",
        "collect-note-comments",
    ]
    assert daily.commands("rednote") == daily.commands("xiaohongshu")
    assert daily.commands("rednote")[0][-1] == "new_followers"


def test_pause_and_live_work_respected_but_old_unknown_is_retained():
    worker = {
        "machine": {},
        "executions": {"old": {"task": {"account_slot": "a"}, "state": "RESULT_UNKNOWN"}},
    }
    product = {"settings": {}}
    config = {"accounts": {"a": {"enabled": True}}}
    assert daily.eligibility(worker, product, "a", config) == "READY"
    assert worker["executions"]["old"]["state"] == "RESULT_UNKNOWN"
    worker["executions"]["old"]["state"] = "RUNNING"
    assert daily.eligibility(worker, product, "a", config) == "DEFERRED"
    worker["machine"]["paused"] = True
    assert daily.eligibility(worker, product, "a", config) == "PAUSED"
    worker["machine"]["paused"] = False
    worker["account_management"] = {"a": {"scheduling_enabled": False}}
    assert daily.eligibility(worker, product, "a", config) == "PAUSED"


def make_collector(tmp_path, monkeypatch):
    c = daily.DailyCollector.__new__(daily.DailyCollector)
    c.root = tmp_path
    c.config = {"accounts": {"a": {"enabled": True}}}
    worker_state = {"machine": {}, "config": {"machine_id": "PC001"}, "executions": {}}
    published = []
    c.worker = SimpleNamespace(
        root=tmp_path,
        _store=SimpleNamespace(snapshot=lambda: worker_state),
        _publish_outbox_record=lambda *args, **kwargs: published.append((args, kwargs)),
    )
    c.product = SimpleNamespace(snapshot=lambda: {"settings": {}})
    monkeypatch.setattr(
        daily, "list_accounts", lambda: [SimpleNamespace(name="a", platform="rednote")]
    )
    return c, published


def test_one_round_per_account_day_and_real_observations_in_outbox(tmp_path, monkeypatch):
    c, published = make_collector(tmp_path, monkeypatch)
    calls = []

    def collect(slot, command):
        calls.append(command)
        return {
            "task": {"state": "SUCCESS"},
            "result": {
                "collection_observations": [
                    {
                        "event_type": "collection_observation",
                        "platform_event_id": command[0],
                        "payload": {"coverage": "complete"},
                    }
                ]
            },
        }

    c.collect = collect
    assert c.run()["accounts"][0]["state"] == "SUCCESS"
    assert c.run()["accounts"][0]["state"] == "ALREADY_RECORDED"
    assert len(calls) == 2
    assert len(published) == 1
    receipt = published[0][0][3]["result"]
    assert len(receipt["incremental_data"]) == 2
    assert len(receipt["business_result"]["channels"]) == 2


def test_outbox_retry_does_not_repeat_collection(tmp_path, monkeypatch):
    c, published = make_collector(tmp_path, monkeypatch)
    calls = []
    c.collect = lambda *args: calls.append(args) or {"task": {"state": "SUCCESS"}}
    c.run()
    path = next(tmp_path.glob("*-daily-passive-*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    record["delivered"] = False
    daily.save(path, record)
    c.run()
    assert len(calls) == 2
    assert len(published) == 2
    assert published[0][1]["record_id"] == published[1][1]["record_id"]


def test_paused_round_writes_receipt_without_browser(tmp_path, monkeypatch):
    c, published = make_collector(tmp_path, monkeypatch)
    c.config["accounts"]["a"]["enabled"] = False
    c.collect = lambda *args: (_ for _ in ()).throw(AssertionError("browser called"))
    assert c.run()["accounts"][0]["state"] == "DISABLED"
    assert published[0][0][3]["result"]["incremental_data"] == []
