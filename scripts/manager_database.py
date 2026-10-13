"""Queryable SQLite projection of complete manager records and their JSON fields."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from manager_store import ManagerStore
from service_errors import ServiceError

_PROJECTION_LOCK = threading.Lock()


class ManagerDatabase:
    """Keep source records intact; expose nested fields through SQLite JSON views."""

    def __init__(self, root: Path) -> None:
        self.path = root / "manager-operations.db"

    def sync_store(self, store: ManagerStore) -> dict:
        """Queue full projections and read the snapshot without holding the state lock."""
        with _PROJECTION_LOCK:
            return self.sync(store.read_view(copy_on_read=False).snapshot())

    def sync(self, state: dict) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=30) as db:
            # Dashboard readers keep seeing the committed snapshot during imports.
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    collection TEXT NOT NULL,
                    record_key TEXT NOT NULL,
                    machine_id TEXT NOT NULL,
                    account_slot TEXT NOT NULL,
                    payload TEXT NOT NULL CHECK(json_valid(payload)),
                    PRIMARY KEY(collection, record_key)
                );
                CREATE INDEX IF NOT EXISTS records_account
                    ON records(machine_id, account_slot, collection);
                CREATE TABLE IF NOT EXISTS live_task_ids (task_id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS live_result_keys (result_key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS daily_metric_facts (
                    machine_id TEXT NOT NULL, account_slot TEXT NOT NULL,
                    business_date TEXT NOT NULL, metric TEXT NOT NULL,
                    actual INTEGER, observed_new INTEGER, current_total REAL,
                    sampled_net_change REAL, newly_discovered INTEGER,
                    coverage TEXT NOT NULL, payload TEXT NOT NULL CHECK(json_valid(payload)),
                    PRIMARY KEY(machine_id, account_slot, business_date, metric)
                );
                CREATE TABLE IF NOT EXISTS worker_records (
                    machine_id TEXT NOT NULL, collection TEXT NOT NULL,
                    record_key TEXT NOT NULL, account_slot TEXT NOT NULL,
                    payload TEXT NOT NULL CHECK(json_valid(payload)),
                    PRIMARY KEY(machine_id, collection, record_key)
                );
                CREATE VIEW IF NOT EXISTS worker_fields AS
                    SELECT r.machine_id, r.collection, r.record_key, r.account_slot,
                           j.fullkey AS path, j.type, j.atom AS value
                    FROM worker_records AS r, json_tree(r.payload) AS j;
                CREATE VIEW IF NOT EXISTS fields AS
                    SELECT r.collection, r.record_key, r.machine_id, r.account_slot,
                           j.fullkey AS path, j.type, j.atom AS value
                    FROM records AS r, json_tree(r.payload) AS j;
                CREATE VIEW IF NOT EXISTS comment_reply_details AS
                    SELECT e.machine_id, e.account_slot,
                           json_extract(e.payload, '$.platform') AS platform,
                           json_extract(e.payload, '$.event_id') AS event_id,
                           json_extract(e.payload, '$.comment_id') AS comment_id,
                           json_extract(e.payload, '$.note_id') AS note_id,
                           json_extract(e.payload, '$.parent_comment_id') AS parent_comment_id,
                           json_extract(e.payload, '$.author_name') AS author_name,
                           json_extract(e.payload, '$.content') AS content,
                           json_extract(e.payload, '$.parent_content') AS parent_content,
                           json_extract(e.payload, '$.detail_state') AS detail_state,
                           json_extract(e.payload, '$.handling_state') AS handling_state,
                           json_extract(d.payload, '$.decision') AS decision,
                           json_extract(d.payload, '$.reason') AS decision_reason,
                           json_extract(r.payload, '$.reply_task_id') AS reply_task_id,
                           json_extract(r.payload, '$.reply_text') AS reply_text,
                           json_extract(r.payload, '$.send_state') AS send_state,
                           json_extract(r.payload, '$.platform_reply_id') AS platform_reply_id,
                           json_extract(r.payload, '$.logical_action_id') AS logical_action_id,
                           json_extract(r.payload, '$.confirmed_at') AS confirmed_at
                    FROM worker_records e
                    LEFT JOIN worker_records d ON d.collection='reply_decisions'
                        AND d.machine_id=e.machine_id AND d.account_slot=e.account_slot
                        AND json_extract(d.payload, '$.platform')=
                            json_extract(e.payload, '$.platform')
                        AND json_extract(d.payload, '$.inbound_event_id')=
                            json_extract(e.payload, '$.event_id')
                    LEFT JOIN worker_records r ON r.collection='reply_records'
                        AND r.machine_id=e.machine_id AND r.account_slot=e.account_slot
                        AND json_extract(r.payload, '$.platform')=
                            json_extract(e.payload, '$.platform')
                        AND json_extract(r.payload, '$.inbound_event_id')=
                            json_extract(e.payload, '$.event_id')
                    WHERE e.collection='inbound_events'
                        AND json_extract(e.payload, '$.event_type')='note_comment';
            """)
            rows = []
            for collection, values in state.items():
                entries = values.items() if isinstance(values, dict) else [("value", values)]
                for key, value in entries:
                    record = value if isinstance(value, dict) else {}
                    request = record.get("request") or record.get("task_request") or {}
                    rows.append(
                        (
                            collection,
                            str(key),
                            str(record.get("machine_id") or ""),
                            str(record.get("account_slot") or request.get("account_slot") or ""),
                            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
                        )
                    )
            # Keep live task and result rows, and update only changed snapshot records.
            live_tasks = {row[0] for row in db.execute("SELECT task_id FROM live_task_ids")}
            live_results = {row[0] for row in db.execute("SELECT result_key FROM live_result_keys")}
            rows = [
                row for row in rows
                if not (row[0] == "tasks" and row[1] in live_tasks)
                and not (row[0] == "results" and row[1] in live_results)
            ]
            db.execute("CREATE TEMP TABLE snapshot_keys (collection TEXT, record_key TEXT, "
                       "PRIMARY KEY(collection, record_key))")
            db.executemany("INSERT INTO snapshot_keys VALUES (?,?)",
                           [(row[0], row[1]) for row in rows])
            db.execute(
                "DELETE FROM records WHERE NOT EXISTS (SELECT 1 FROM snapshot_keys k "
                "WHERE k.collection=records.collection AND k.record_key=records.record_key) "
                "AND NOT EXISTS (SELECT 1 FROM live_task_ids WHERE task_id=records.record_key "
                "AND records.collection='tasks') "
                "AND NOT EXISTS (SELECT 1 FROM live_result_keys "
                "WHERE result_key=records.record_key "
                "AND records.collection='results')"
            )
            db.executemany(
                "INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(collection,record_key) "
                "DO UPDATE SET machine_id=excluded.machine_id, account_slot=excluded.account_slot, "
                "payload=excluded.payload WHERE records.payload != excluded.payload "
                "OR records.machine_id != excluded.machine_id "
                "OR records.account_slot != excluded.account_slot", rows,
            )
            synced_at = datetime.now(UTC).isoformat()
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('synced_at', ?)", (synced_at,))
        self.sync_daily_metrics(state)
        return {
            "success": True,
            "database": str(self.path),
            "records": len(rows) + len(live_tasks) + len(live_results),
            "synced_at": synced_at,
        }

    def import_worker(self, machine_id: str, snapshot: dict) -> dict:
        if snapshot.get("success") is not True:
            raise ServiceError("WORKER_DATA_UNAVAILABLE", "员工业务数据库尚未成功导出")
        rows = []
        for table, values in snapshot["tables"].items():
            for index, value in enumerate(values):
                rows.append(
                    (
                        machine_id,
                        table,
                        str(index),
                        str(value.get("account_slot") or ""),
                        json.dumps(value, ensure_ascii=False),
                    )
                )
        rows.append(
            (
                machine_id,
                "_schema",
                "schema",
                "",
                json.dumps(snapshot["schema"], ensure_ascii=False),
            )
        )
        if snapshot.get("product_state") is not None:
            rows.append(
                (
                    machine_id,
                    "_product_state",
                    "state",
                    "",
                    json.dumps(snapshot["product_state"], ensure_ascii=False),
                )
            )
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("DELETE FROM worker_records WHERE machine_id=?", (machine_id,))
            db.executemany("INSERT INTO worker_records VALUES (?, ?, ?, ?, ?)", rows)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES (?, ?)",
                (
                    f"worker:{machine_id}",
                    json.dumps(
                        {
                            "exported_at": snapshot["exported_at"],
                            "source": snapshot["source"],
                            "imported_at": datetime.now(UTC).isoformat(),
                            "records": len(rows),
                        }
                    ),
                ),
            )
        return {
            "success": True,
            "machine_id": machine_id,
            "records": len(rows),
            "tables": len(snapshot["tables"]),
            "exported_at": snapshot["exported_at"],
        }

    def sync_collection_results(self, results: list[dict]) -> None:
        """Persist an immediate collection without rebuilding historical projections."""
        with _PROJECTION_LOCK:
            self._sync_collection_results(results)

    def _sync_collection_results(self, results: list[dict]) -> None:
        if not self.path.exists():
            self.sync({})
        if not results:
            return
        rows = [
            ("results", result["record_id"].removeprefix("result:"),
             result["machine_id"], result.get("account_slot", ""),
             json.dumps(result, ensure_ascii=False, separators=(",", ":")))
            for result in results
        ]
        with sqlite3.connect(self.path, timeout=30) as db:
            db.executemany("INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)", rows)
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('collection_synced_at', ?)",
                (datetime.now(UTC).isoformat(),),
            )

    def query(self, sql: str, parameters: list[Any] | None = None, limit: int = 200) -> dict:
        if not sql.strip() or not 1 <= limit <= 10000:
            raise ServiceError("INVALID_QUERY", "请输入查询语句，返回行数为 1–10000")
        try:
            with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
                db.row_factory = sqlite3.Row
                db.execute("PRAGMA query_only=ON")
                # Permit SQL reads/functions, including JSON traversal and aggregates.
                allowed = {
                    sqlite3.SQLITE_SELECT,
                    sqlite3.SQLITE_READ,
                    sqlite3.SQLITE_FUNCTION,
                    sqlite3.SQLITE_RECURSIVE,
                }
                db.set_authorizer(
                    lambda action, *_: (
                        sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
                    )
                )
                steps = 0

                def bounded() -> int:
                    nonlocal steps
                    steps += 1
                    return int(steps > 10000)

                db.set_progress_handler(bounded, 1000)
                cursor = db.execute(sql, parameters or [])
                rows = cursor.fetchmany(limit + 1)
                return {
                    "success": True,
                    "database": str(self.path),
                    "columns": [c[0] for c in cursor.description or []],
                    "rows": [dict(row) for row in rows[:limit]],
                    "truncated": len(rows) > limit,
                    "limit": limit,
                }
        except sqlite3.Error as exc:
            raise ServiceError("DATABASE_QUERY_FAILED", f"数据库只读查询失败：{exc}") from exc

    def schema(self) -> dict:
        result = self.query(
            "SELECT 'manager' AS source, collection, path, type, count(*) AS occurrences "
            "FROM fields GROUP BY collection, path, type UNION ALL "
            "SELECT machine_id, collection, path, type, count(*) FROM worker_fields "
            "GROUP BY machine_id, collection, path, type ORDER BY source, collection, path",
            limit=10000,
        )
        result["tables"] = {
            "records": "每条原始记录；payload 保留全部嵌套字段，可用 json_extract 查询",
            "fields": "全部 JSON 路径、类型和值；数组下标保留",
            "metadata": "数据库同步时间",
            "worker_records": "员工运营库全部表字段与本地产品状态；按机器隔离",
            "worker_fields": "员工记录嵌套 JSON 路径；_schema 保存空表字段定义",
            "comment_reply_details": "按机器、账号关联评论、处理决策和发送确认记录",
            "daily_metric_facts": (
                "按日指标：actual已确认采集范围计数、observed_new已观察日新增、current_total存量、"
                "sampled_net_change采样净变、newly_discovered新入库；payload含区间和依据"
            ),
        }
        return result

    def employee_observations(self) -> list[dict]:
        """Read complete imported events for the account views, without refreshing the DB."""
        if not self.path.exists():
            return []
        with sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True) as db:
            rows = db.execute(
                "SELECT machine_id, account_slot, payload FROM worker_records "
                "WHERE collection IN ('inbound_events', 'metric_snapshots')"
            ).fetchall()
            related = db.execute(
                "SELECT machine_id, account_slot, collection, payload FROM worker_records "
                "WHERE collection IN ('reply_records', 'reply_decisions', "
                "'reply_rules', 'reply_rule_versions')"
            ).fetchall()
        groups = {}
        for machine, slot, collection, raw in related:
            row = json.loads(raw)
            scope = (machine, slot, row.get("platform", "xiaohongshu"))
            groups.setdefault((*scope, collection), []).append(row)

        def rule_for(scope: tuple, record: dict) -> dict | None:
            rule_id, version = record.get("rule_id"), record.get("rule_version")
            if not rule_id or not version:
                return None
            return next((r for r in groups.get((*scope, "reply_rule_versions"), [])
                         if r.get("rule_id") == rule_id and r.get("rule_version") == version), None)

        observations = []
        for machine, slot, raw in rows:
            event = json.loads(raw)
            payload = event.pop("payload_json", None)
            if payload is not None:
                event["payload"] = json.loads(payload) if isinstance(payload, str) else payload
            extra = event.pop("extra_json", None)
            if extra is not None:
                event["extra"] = json.loads(extra) if isinstance(extra, str) else extra
            if event.get("event_type") == "private_message":
                scope = (machine, slot, event.get("platform", "xiaohongshu"))
                event_id = event.get("event_id")
                replies = [dict(r) for r in groups.get((*scope, "reply_records"), [])
                           if r.get("inbound_event_id") == event_id]
                replies.sort(key=lambda r: r.get("sent_at") or r.get("updated_at") or "")
                decisions = [r for r in groups.get((*scope, "reply_decisions"), [])
                             if r.get("inbound_event_id") == event_id]
                decision = max(decisions, key=lambda r: r.get("updated_at") or "", default={})
                for reply in replies:
                    reply["processing_rule"] = rule_for(scope, reply)
                event["reply_history"] = replies
                event["processing_decision"] = decision
                applied = decision or (replies[-1] if replies else {})
                event["processing_rule"] = rule_for(scope, applied)
                event["processing_rule_basis"] = "applied" if applied else "current"
                if not applied:
                    event["processing_rule"] = max(
                        groups.get((*scope, "reply_rules"), []),
                        key=lambda r: r.get("updated_at") or "", default=None,
                    )
            observations.append({"machine_id": machine, "account_slot": slot, "data": event})
        return observations

    def sync_daily_metrics(self, state: dict) -> None:
        """Rebuild semantic facts from preserved receipts and imported worker evidence."""
        from employee_operations import EmployeeOperations
        from manager_service import ManagerService

        latest: dict[str, dict] = {}
        for result in state.get("results", {}).values():
            key = ManagerService._result_execution_key(result)
            if key not in latest or ManagerService._result_revision_key(result) > (
                    ManagerService._result_revision_key(latest[key])):
                latest[key] = result
        operations = EmployeeOperations(
            {"daily_plans": {}, "incremental_records": {}, **state,
             "worker_observations": self.employee_observations()},
            list(latest.values()), ManagerService._result_metric_counts,
        )
        accounts = list(state.get("accounts", {}).values())
        days = operations.summary(accounts)["daily_metrics"]
        rows = []
        for day in days:
            for kind, value in day["metrics"].items():
                rows.append((day["machine_id"], day["account_slot"], day["date"], kind,
                             value.get("actual"), value.get("observed_new"),
                             value.get("current_total"),
                             (value.get("sampled_net_change") or {}).get("value"),
                             value.get("newly_discovered"), value.get("coverage", "execution"),
                             json.dumps(value, ensure_ascii=False)))
        with sqlite3.connect(self.path, timeout=30) as db:
            db.execute("DELETE FROM daily_metric_facts")
            db.executemany("INSERT INTO daily_metric_facts VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
            db.execute("INSERT OR REPLACE INTO metadata VALUES ('metric_semantics_version','3')")
