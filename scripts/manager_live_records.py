"""SQLite authority for tasks dispatched after the manager cutover."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


class ManagerLiveRecords:
    def __init__(self, root: Path) -> None:
        self.path = root / "manager-operations.db"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                collection TEXT NOT NULL,
                record_key TEXT NOT NULL,
                machine_id TEXT NOT NULL,
                account_slot TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),
                PRIMARY KEY(collection, record_key)
            );
            CREATE TABLE IF NOT EXISTS live_task_ids (task_id TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS live_result_keys (result_key TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS worker_records (
                machine_id TEXT NOT NULL,
                collection TEXT NOT NULL,
                record_key TEXT NOT NULL,
                account_slot TEXT NOT NULL,
                payload TEXT NOT NULL CHECK(json_valid(payload)),
                PRIMARY KEY(machine_id, collection, record_key)
            );
        """)
        return connection

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=30)
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        db = self._connect()
        try:
            with db:
                db.execute("BEGIN IMMEDIATE")
                yield db
        finally:
            db.close()

    @staticmethod
    def _initialized(db: sqlite3.Connection) -> bool:
        return (
            db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='live_task_ids'"
            ).fetchone()
            is not None
        )

    def load(self) -> tuple[dict[str, dict], dict[str, dict]]:
        if not self.path.exists():
            return {}, {}
        with self._read() as db:
            if not self._initialized(db):
                return {}, {}
            tasks = {
                key: json.loads(payload)
                for key, payload in db.execute(
                    "SELECT r.record_key,r.payload FROM records r "
                    "JOIN live_task_ids t ON t.task_id=r.record_key "
                    "WHERE r.collection='tasks'"
                )
            }
            results = {
                key: json.loads(payload)
                for key, payload in db.execute(
                    "SELECT r.record_key,r.payload FROM records r "
                    "JOIN live_result_keys t ON t.result_key=r.record_key "
                    "WHERE r.collection='results'"
                )
            }
        return tasks, results

    def get(self, collection: str, key: str) -> dict | None:
        if not self.path.exists():
            return None
        with self._read() as db:
            if not self._initialized(db):
                return None
            return self._get(db, collection, key)

    @staticmethod
    def _get(db: sqlite3.Connection, collection: str, key: str) -> dict | None:
        table, column = (
            ("live_task_ids", "task_id")
            if collection == "tasks"
            else ("live_result_keys", "result_key")
        )
        row = db.execute(
            f"SELECT r.payload FROM records r JOIN {table} t ON t.{column}=r.record_key "
            "WHERE r.collection=? AND r.record_key=?",
            (collection, key),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def load_collection(self, collection: str) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        table, column = (
            ("live_task_ids", "task_id")
            if collection == "tasks"
            else ("live_result_keys", "result_key")
        )
        with self._read() as db:
            if not self._initialized(db):
                return {}
            return {
                key: json.loads(payload)
                for key, payload in db.execute(
                    f"SELECT r.record_key,r.payload FROM records r JOIN {table} t "
                    f"ON t.{column}=r.record_key WHERE r.collection=?",
                    (collection,),
                )
            }

    def update_task(self, task_id: str, mutator: Callable[[dict], dict]) -> dict | None:
        with self._write() as db:
            current = self._get(db, "tasks", task_id)
            if current is None:
                return None
            updated = mutator(current)
            self._put(db, "tasks", task_id, updated)
            return updated

    def record_result(self, key: str, result: dict) -> tuple[dict, bool]:
        with self._write() as db:
            existing = self._get(db, "results", key)
            if existing is not None:
                return existing, False
            db.execute("INSERT OR IGNORE INTO live_result_keys VALUES (?)", (key,))
            self._put(db, "results", key, result)
            return result, True

    def has_task(self, task_id: str) -> bool:
        if not self.path.exists():
            return False
        with self._read() as db:
            if not self._initialized(db):
                return False
            return (
                db.execute("SELECT 1 FROM live_task_ids WHERE task_id=?", (task_id,)).fetchone()
                is not None
            )

    def has_execution(self, machine_id: str, remote_execution_id: str) -> bool:
        if not remote_execution_id or not self.path.exists():
            return False
        with self._read() as db:
            if not self._initialized(db):
                return False
            return (
                db.execute(
                    "SELECT 1 FROM records r JOIN live_task_ids t ON t.task_id=r.record_key "
                    "WHERE r.collection='tasks' "
                    "AND r.machine_id=? "
                    "AND json_extract(r.payload,'$.remote_execution_id')=? LIMIT 1",
                    (machine_id, remote_execution_id),
                ).fetchone()
                is not None
            )

    def commit(
        self,
        tasks: dict[str, dict],
        results: dict[str, dict],
        *,
        original_tasks: dict[str, dict] | None = None,
        original_results: dict[str, dict] | None = None,
    ) -> None:
        if not tasks and not results:
            return
        with self._write() as db:
            for task_id, task in tasks.items():
                if original_tasks is not None and task_id in original_tasks:
                    current = self._get(db, "tasks", task_id) or {}
                    incoming_business_state = task.get("business_state")
                    task = self._merge_changes(
                        current,
                        original_tasks[task_id],
                        task,
                        prefer_current=str(current.get("updated_at") or "")
                        > str(task.get("updated_at") or ""),
                    )
                    if current.get("business_state") and not incoming_business_state:
                        # ACK/progress observed from an older batch cannot reopen settled work.
                        for field in (
                            "state",
                            "dispatch_state",
                            "business_state",
                            "last_known_remote_state",
                            "reconciliation_required",
                            "updated_at",
                            "error_code",
                            "message",
                            "result_summary",
                        ):
                            if field in current:
                                task[field] = current[field]
                    if "dispatch_state" in task or "business_state" in task:
                        task["state"] = task.get("business_state") or task.get("dispatch_state")
                db.execute("INSERT OR IGNORE INTO live_task_ids VALUES (?)", (task_id,))
                self._put(db, "tasks", task_id, task)
            for result_key, result in results.items():
                if (
                    original_results is not None
                    and result_key not in original_results
                    and self._get(db, "results", result_key) is not None
                ):
                    continue
                if original_results is not None and result_key in original_results:
                    result = self._merge_changes(
                        self._get(db, "results", result_key) or {},
                        original_results[result_key],
                        result,
                    )
                db.execute("INSERT OR IGNORE INTO live_result_keys VALUES (?)", (result_key,))
                self._put(db, "results", result_key, result)

    @staticmethod
    def _merge_changes(
        current: dict, original: dict, updated: dict, *, prefer_current: bool = False
    ) -> dict:
        """Apply batch field edits to the latest row after concurrent SQLite updates."""
        merged = dict(current)
        for key in original.keys() - updated.keys():
            if prefer_current and current.get(key) != original[key]:
                continue
            merged.pop(key, None)
        for key, value in updated.items():
            if key not in original or original[key] != value:
                if all(isinstance(row.get(key), dict) for row in (current, original, updated)):
                    merged[key] = ManagerLiveRecords._merge_changes(
                        current[key], original[key], value, prefer_current=prefer_current
                    )
                elif (
                    prefer_current
                    and key in current
                    and (key not in original or current[key] != original[key])
                ):
                    continue
                else:
                    merged[key] = value
        return merged

    @staticmethod
    def _put(db: sqlite3.Connection, collection: str, key: str, row: dict) -> None:
        request = row.get("request") or row.get("task_request") or {}
        db.execute(
            "INSERT OR REPLACE INTO records VALUES (?,?,?,?,?)",
            (
                collection,
                key,
                str(row.get("machine_id") or ""),
                str(row.get("account_slot") or request.get("account_slot") or ""),
                json.dumps(row, ensure_ascii=False, separators=(",", ":")),
            ),
        )
