from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.collector_service import CollectorService
from scripts.comment_collector import CommentCollector
from scripts.comment_relations import send_projection
from scripts.inbound_event_service import InboundEventService
from scripts.manager_database import ManagerDatabase
from scripts.operations_db import _MIGRATIONS, OperationsDatabase
from scripts.reply_record_service import ReplyRecordService
from scripts.worker_data_export import export_business_data


def test_v9_migration_preserves_payload_and_reopens(tmp_path) -> None:
    path = tmp_path / "operations.db"
    payload = json.dumps({"comment_id": "c", "parent_comment_id": "p", "content": "回复"})
    with sqlite3.connect(path) as db:
        for version in range(1, 10):
            db.executescript(_MIGRATIONS[version])
        db.execute("PRAGMA user_version=9")
        db.execute(
            """INSERT INTO inbound_events(event_id,account_slot,event_type,platform_event_id,
            occurred_at,discovered_at,payload_json) VALUES('e','a','note_comment','n','t','t',?)""",
            (payload,),
        )
    database = OperationsDatabase(path=path)
    row = database.fetch_one("SELECT * FROM inbound_events")
    assert row["payload_json"] == payload
    assert row["comment_id"] == "c" and row["parent_comment_id"] == "p"
    assert row["handling_state"] == "NEW"
    assert row["detail_state"] == "PARTIAL"
    assert (
        OperationsDatabase(path=path).fetch_one("SELECT count(*) n FROM inbound_events")["n"] == 1
    )


def test_batches_enrich_without_reopening_and_manager_join(tmp_path) -> None:
    root = tmp_path / "worker"
    database = OperationsDatabase(root)
    events = InboundEventService(database)
    collector = CommentCollector(events, CollectorService(database))
    comment = {"notification_id": "n", "occurred_at": "t", "content": "你好"}
    first = collector.ingest(account_slot="a", comments=[comment], task_id="t")
    event_id = first["created_events"][0]["event_id"]
    events.mark_handled(event_id)
    second = collector.ingest(
        account_slot="a",
        comments=[
            {
                **comment,
                "comment_id": "c",
                "feed_id": "f",
                "parent_comment_id": "p",
            }
        ],
    )
    assert second["created_count"] == 0
    assert events.get(event_id)["handling_state"] == "HANDLED"
    assert events.get(event_id)["detail_state"] == "AVAILABLE"
    assert database.fetch_one("SELECT count(*) n FROM comment_batch_events")["n"] == 2
    empty = collector.ingest(account_slot="b", comments=[])
    batch = database.fetch_one(
        "SELECT * FROM comment_collection_batches WHERE batch_id=?", (empty["batch_id"],)
    )
    assert batch["observed_count"] == 0 and batch["coverage"] == "UNKNOWN"
    manager = ManagerDatabase(tmp_path / "manager")
    manager.sync({})
    manager.import_worker("PC057", export_business_data(root))
    manager.import_worker("PC052", export_business_data(root))
    rows = manager.query("SELECT * FROM comment_reply_details")["rows"]
    assert len(rows) == 2
    assert rows[0]["parent_comment_id"] == "p" and rows[0]["send_state"] is None


@pytest.mark.parametrize(
    ("state", "readback", "expected"),
    [
        ("SUCCESS", {"reply_found": True}, "CONFIRMED"),
        ("SUCCESS", {"acknowledgement": "editor-closed"}, "AWAITING_RECONCILIATION"),
        ("RESULT_UNKNOWN", {"reply_found": True}, "AWAITING_RECONCILIATION"),
        ("BLOCKED", {}, "NOT_SENT"),
        ("FAILED", {}, "FAILED"),
    ],
)
def test_send_state_requires_confirmation(state, readback, expected) -> None:
    assert send_projection(state, readback)[0] == expected


def test_failed_ingestion_retains_batch(tmp_path) -> None:
    database = OperationsDatabase(tmp_path)
    collector = CommentCollector(InboundEventService(database), CollectorService(database))
    with pytest.raises(Exception, match="评论事件必须"):
        collector.ingest(account_slot="a", comments=[{}])
    batch = database.fetch_one("SELECT * FROM comment_collection_batches")
    assert batch["result_state"] == "FAILED" and batch["observed_count"] is None


def test_reply_reconciliation_updates_projection(tmp_path) -> None:
    database = OperationsDatabase(tmp_path)
    replies = ReplyRecordService(database)
    record = replies.record(
        {
            "task_id": "t",
            "state": "RESULT_UNKNOWN",
            "account_slot": "a",
            "parameters": {"logical_action_id": "goal-1", "persona_version": "v1"},
        },
        draft={},
        source_event={"event_type": "private_message", "event_id": "e"},
    )
    assert record["send_state"] == "AWAITING_RECONCILIATION"
    assert record["logical_action_id"] == "goal-1"
    record = replies.reconcile_result_unknown("t", readback={"matched": True})
    assert record["send_state"] == "CONFIRMED" and record["confirmed_at"]


def test_migration_failure_rolls_back_schema(tmp_path, monkeypatch) -> None:
    path = tmp_path / "operations.db"
    with sqlite3.connect(path) as db:
        for version in range(1, 10):
            db.executescript(_MIGRATIONS[version])
        db.execute("PRAGMA user_version=9")
    monkeypatch.setitem(_MIGRATIONS, 10, _MIGRATIONS[10] + "INVALID SQL;")
    with pytest.raises(sqlite3.Error):
        OperationsDatabase(path=path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 9
        assert "comment_id" not in [r[1] for r in db.execute("PRAGMA table_info(inbound_events)")]
