from __future__ import annotations

import json
from unittest.mock import Mock

import pytest
from xhs.creator_metrics import collect_creator_metrics, parse_creator_card
from xhs.errors import XHSError

NOTE_ID = "6aaa367d0000000025034241"


def card(text: str = "119\n10\n7\n0\n0") -> dict:
    return {"impression": json.dumps({"noteTarget": {"value": {"noteId": NOTE_ID}}}),
            "title": "作品", "text": f"审核中\n作品\n2026-09-16 14:26\n{text}"}


def test_creator_counter_order_and_zero() -> None:
    result = parse_creator_card(card(), platform="rednote")
    assert result["metrics"] == {
        "views": 119, "comments": 10, "likes": 7, "favorites": 0, "shares": 0,
    }
    assert "rednote.com/discovery/item/" in result["note_url"]


def test_missing_counters_stay_unknown() -> None:
    assert all(value is None for value in parse_creator_card(card("1\n2"))["metrics"].values())
    assert parse_creator_card(card("1.2万\n0\n7\n0\n—"))["metrics"]["views"] == 12000
    assert parse_creator_card(card("1.2万\n0\n7\n0\n—"))["metrics"]["shares"] is None


def test_targeted_collection_reuses_creator_page_without_audit_wait() -> None:
    page = Mock(platform="rednote")
    page.evaluate.side_effect = ["https://creator.rednote.com/new/note-manager", [card()]]
    result = collect_creator_metrics(page, note_ids=[NOTE_ID])
    assert result["count"] == 1
    assert not result["partial"]
    page.navigate.assert_not_called()
    assert "account" not in result


def test_missing_target_is_explicit(monkeypatch) -> None:
    monkeypatch.setattr("xhs.creator_metrics.time.sleep", lambda _: None)
    page = Mock(platform="xiaohongshu")
    page.evaluate.side_effect = lambda script: (
        "https://creator.xiaohongshu.com/new/note-manager" if script == "location.href"
        else [card()] if "data-impression" in script else None
    )
    result = collect_creator_metrics(page, note_ids=["a" * 24])
    assert result["partial"]
    assert result["missing_note_ids"] == ["a" * 24]
    assert result["notes"] == []


def test_unavailable_page_is_not_zero(monkeypatch) -> None:
    monkeypatch.setattr("xhs.creator_metrics.time.sleep", lambda _: None)
    page = Mock(platform="xiaohongshu")
    page.evaluate.return_value = []
    with pytest.raises(XHSError, match="待更新"):
        collect_creator_metrics(page)


def test_confirmed_empty_creator_inventory(monkeypatch) -> None:
    monkeypatch.setattr("xhs.creator_metrics.time.sleep", lambda _: None)
    page = Mock(platform="xiaohongshu")
    page.evaluate.side_effect = lambda script: (
        "https://creator.xiaohongshu.com/new/note-manager" if script == "location.href"
        else True if "没有找到相关笔记" in script else []
    )
    result = collect_creator_metrics(page)
    assert result["notes"] == []
    assert result["inventory_complete"] is True
    assert result["partial"] is False


@pytest.mark.parametrize("platform", ["xiaohongshu", "rednote"])
def test_business_adapter_routes_creator_without_profile(monkeypatch, platform) -> None:
    import business_runner
    from xhs import creator_metrics

    monkeypatch.setattr(business_runner, "load_account", lambda *a, **kw: Mock(
        platform=platform, name="a", bridge_url="local", account_id="a", bridge_token=""))
    monkeypatch.setattr(business_runner, "BridgePage", Mock())
    collect = Mock(return_value={"source": "creator_manager", "notes": []})
    monkeypatch.setattr(creator_metrics, "collect_creator_metrics", collect)
    result = business_runner.BusinessRunner._execute_existing_adapter(
        "a", "collect-operations-metrics", {"source": "creator_manager", "note_ids": [NOTE_ID]})
    assert result["source"] == "creator_manager"
    assert collect.call_args.kwargs["note_ids"] == [NOTE_ID]


def test_creator_snapshot_and_worker_receipt_preserve_note_metrics(tmp_path) -> None:
    from application_service import ApplicationService
    from manager_worker_service import ManagerWorkerService
    from metric_service import MetricService
    from operations_db import OperationsDatabase

    service = ApplicationService.__new__(ApplicationService)
    service.metrics = MetricService(OperationsDatabase(path=tmp_path / "metrics.db"))
    service.inbound_events = Mock()
    result = {"source": "creator_manager", "captured_at": "2026-09-16T07:00:00+00:00",
              "notes": [parse_creator_card(card())], "count": 1}
    snapshots = service._record_metric_collection("a", result)
    assert snapshots["account"] is None
    assert snapshots["notes"][0]["snapshot"]["views"] == 119
    assert snapshots["notes"][0]["snapshot"]["favorites"] == 0
    receipt = ManagerWorkerService._normalize_outcome({"state": "SUCCESS", "result": result})
    assert receipt["business_result"] == result
