"""Persistent manager-side registry for executors, tasks, transfers, and results."""

from __future__ import annotations

import copy
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TypeVar

import orjson
from manager_live_records import ManagerLiveRecords
from run_lock import RunLock

MANAGER_STORE_SCHEMA_VERSION = 1
_PROCESS_LOCK = threading.RLock()
T = TypeVar("T")


def manager_root() -> Path:
    override = os.getenv("XHS_MANAGER_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".xhs" / "auto-xhs" / "manager"


def _default_state() -> dict:
    return {
        "schema_version": MANAGER_STORE_SCHEMA_VERSION,
        "settings": {},
        "machines": {},
        "accounts": {},
        "account_alerts": {},
        "personas": {},
        "goals": {},
        "daily_plans": {},
        "operation_tasks": {},
        "agent_messages": {},
        "tasks": {},
        "schedules": {},
        "operation_reports": {},
        "transfers": {},
        "versions": {},
        "results": {},
        "action_attributions": {},
        "incremental_records": {},
        "evidence_downloads": {},
        "sync_status": {},
        "collection_cursors": {},
        "debug_incidents": {},
        "debug_releases": {},
    }


class ManagerStore:
    """Atomic JSON store kept separate from account and browser runtime data."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else manager_root()
        self.path = self.root / "manager-state.json"
        self.lock_path = self.root / "manager-state.lock"
        self._batch = threading.local()
        self.live = ManagerLiveRecords(self.root)
        self._cache_signature: tuple[int, int] | None = None
        self._cache_state: dict | None = None

    @contextmanager
    def batch(self) -> Iterator[None]:
        """Commit related local updates with one state-file write."""
        if getattr(self._batch, "state", None) is not None:
            yield
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            lock = RunLock(str(self.lock_path))
            if not lock.acquire(timeout=10):
                raise TimeoutError("经理机数据正在被其他进程使用")
            try:
                self._batch.state = self._read_state_fresh()
                original_result_keys = set(self._batch.state["results"])
                live_tasks, live_results = self.live.load()
                self._batch.state["tasks"].update(live_tasks)
                self._batch.state["results"].update(live_results)
                self._batch.original_live_tasks = copy.deepcopy(live_tasks)
                self._batch.original_live_results = copy.deepcopy(live_results)
                self._batch.original_result_keys = original_result_keys
                self._batch.dirty = False
                self._batch.live_tasks = set()
                self._batch.live_results = set()
                try:
                    yield
                finally:
                    try:
                        state = self._batch.state
                        self._batch.live_tasks.update(
                            key
                            for key, old in self._batch.original_live_tasks.items()
                            if state["tasks"].get(key) != old
                        )
                        self._batch.live_results.update(
                            key
                            for key, old in self._batch.original_live_results.items()
                            if state["results"].get(key) != old
                        )
                        live_task_ids = (
                            set(self._batch.original_live_tasks) | self._batch.live_tasks
                        )
                        self._batch.live_results.update(
                            key
                            for key, row in state["results"].items()
                            if key not in self._batch.original_result_keys
                            and key not in self._batch.original_live_results
                            and str(row.get("task_id") or "") in live_task_ids
                        )
                        self.live.commit(
                            {key: state["tasks"][key] for key in self._batch.live_tasks},
                            {key: state["results"][key] for key in self._batch.live_results},
                        )
                        if self._batch.dirty:
                            live_tasks, live_results = self.live.load()
                            state["tasks"] = {
                                key: row
                                for key, row in state["tasks"].items()
                                if key not in live_tasks
                            }
                            state["results"] = {
                                key: row
                                for key, row in state["results"].items()
                                if key not in live_results
                            }
                            self._write_state(state)
                    finally:
                        del self._batch.state
                        del self._batch.dirty
                        del self._batch.live_tasks
                        del self._batch.live_results
                        del self._batch.original_live_tasks
                        del self._batch.original_live_results
                        del self._batch.original_result_keys
            finally:
                lock.release()

    def snapshot(self) -> dict:
        return copy.deepcopy(self._read_current_state())

    def read_view(
        self,
        *,
        excluded_fields: frozenset[str] = frozenset(),
        copy_on_read: bool = True,
    ) -> ManagerStore:
        """Read the atomically replaced file without waiting for background writers."""
        state = self._with_live(self._read_state())
        if excluded_fields:
            state = _project_fields(state, excluded_fields)
        return _ManagerReadView(self.root, state, copy_on_read=copy_on_read)

    def mutate(self, callback: Callable[[dict], T]) -> T:
        """Commit a linked plan, goal and operation task together."""
        return self._transaction(callback, write=True)

    def get(self, collection: str, item_id: str) -> dict | None:
        self._check_collection(collection)
        return copy.deepcopy(self._read_current_state()[collection].get(item_id))

    def list(self, collection: str) -> list[dict]:
        self._check_collection(collection)
        return [copy.deepcopy(item) for item in self._read_current_state()[collection].values()]

    def put(self, collection: str, item_id: str, item: dict) -> dict:
        self._check_collection(collection)
        if collection == "tasks" and item_id not in self._read_state()["tasks"]:
            record = copy.deepcopy(item)
            state = getattr(self._batch, "state", None)
            if state is not None:
                state["tasks"][item_id] = record
                self._batch.live_tasks.add(item_id)
            else:
                self._commit_live({item_id: record}, {})
            return copy.deepcopy(record)

        def mutate(state: dict) -> dict:
            state[collection][item_id] = copy.deepcopy(item)
            return copy.deepcopy(item)

        return self._transaction(mutate, write=True)

    def delete(self, collection: str, item_id: str) -> None:
        self._check_collection(collection)
        self._transaction(lambda state: state[collection].pop(item_id, None), write=True)

    def update(
        self,
        collection: str,
        item_id: str,
        mutator: Callable[[dict], dict],
    ) -> dict | None:
        self._check_collection(collection)
        if collection == "tasks" and (
            item_id in getattr(self._batch, "live_tasks", set()) or self.live.has_task(item_id)
        ):
            state = getattr(self._batch, "state", None)
            if state is not None:
                current = self.get("tasks", item_id)
                if current is None:
                    return None
                updated = copy.deepcopy(mutator(current))
                state["tasks"][item_id] = updated
                self._batch.live_tasks.add(item_id)
            else:
                with _PROCESS_LOCK:
                    lock = RunLock(str(self.lock_path))
                    if not lock.acquire(timeout=10):
                        raise TimeoutError("经理机数据正在被其他进程使用")
                    try:
                        current = self.get("tasks", item_id)
                        if current is None:
                            return None
                        updated = copy.deepcopy(mutator(current))
                        self.live.commit({item_id: updated}, {})
                    finally:
                        lock.release()
            return copy.deepcopy(updated)

        def mutate(state: dict) -> dict | None:
            current = state[collection].get(item_id)
            if current is None:
                return None
            updated = mutator(copy.deepcopy(current))
            state[collection][item_id] = copy.deepcopy(updated)
            return copy.deepcopy(updated)

        return self._transaction(mutate, write=True)

    def get_collection_cursor(self, machine_id: str) -> int:
        return int(self._read_current_state()["collection_cursors"].get(machine_id, 0))

    def set_collection_cursor(self, machine_id: str, sequence: int) -> int:
        def mutate(state: dict) -> int:
            current = int(state["collection_cursors"].get(machine_id, 0))
            state["collection_cursors"][machine_id] = max(current, int(sequence))
            return int(state["collection_cursors"][machine_id])

        return self._transaction(mutate, write=True)

    def record_result(self, result_key: str, result: dict) -> tuple[dict, bool]:
        """Insert one immutable remote result and report whether it was new."""

        task_id = str(result.get("task_id") or "")
        if (
            task_id
            and (
                task_id in getattr(self._batch, "live_tasks", set()) or self.live.has_task(task_id)
            )
        ) or self.live.has_execution(
            str(result.get("machine_id") or ""),
            str(result.get("remote_execution_id") or ""),
        ):
            existing = self.get("results", result_key)
            if existing is not None:
                return existing, False
            record = copy.deepcopy(result)
            state = getattr(self._batch, "state", None)
            if state is not None:
                state["results"][result_key] = record
                self._batch.live_results.add(result_key)
            else:
                self._commit_live({}, {result_key: record})
            return copy.deepcopy(record), True

        def mutate(state: dict) -> tuple[dict, bool]:
            existing = state["results"].get(result_key)
            if existing is not None:
                return copy.deepcopy(existing), False
            state["results"][result_key] = copy.deepcopy(result)
            return copy.deepcopy(result), True

        return self._transaction(mutate, write=True)

    def record_incremental(self, record_key: str, record: dict) -> tuple[dict, bool]:
        """Insert one natural-keyed incremental record without duplicating replays."""

        def mutate(state: dict) -> tuple[dict, bool]:
            existing = state["incremental_records"].get(record_key)
            if existing is not None:
                return copy.deepcopy(existing), False
            state["incremental_records"][record_key] = copy.deepcopy(record)
            return copy.deepcopy(record), True

        return self._transaction(mutate, write=True)

    def _transaction(self, callback: Callable[[dict], T], *, write: bool) -> T:
        state = getattr(self._batch, "state", None)
        if state is not None:
            result = callback(state)
            if write:
                self._batch.dirty = True
            return result
        if write:
            with self.batch():
                result = callback(self._batch.state)
                self._batch.dirty = True
                return result
        self.root.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            lock = RunLock(str(self.lock_path))
            if not lock.acquire(timeout=10):
                raise TimeoutError("经理机数据正在被其他进程使用")
            try:
                state = self._read_state_fresh()
                result = callback(state)
                if write:
                    self._write_state(state)
                return result
            finally:
                lock.release()

    def _read_current_state(self) -> dict:
        """Read a consistent atomic file without waiting for background writers."""
        state = getattr(self._batch, "state", None)
        return state if state is not None else self._with_live(self._read_state())

    def _with_live(self, state: dict) -> dict:
        tasks, results = self.live.load()
        return {
            **state,
            "tasks": {**state["tasks"], **tasks},
            "results": {**state["results"], **results},
        }

    def _commit_live(self, tasks: dict[str, dict], results: dict[str, dict]) -> None:
        with _PROCESS_LOCK:
            lock = RunLock(str(self.lock_path))
            if not lock.acquire(timeout=10):
                raise TimeoutError("经理机数据正在被其他进程使用")
            try:
                self.live.commit(tasks, results)
            finally:
                lock.release()

    def _read_state(self) -> dict:
        if not self.path.exists():
            return _default_state()
        try:
            info = self.path.stat()
            signature = (info.st_mtime_ns, info.st_size)
            if signature == self._cache_signature and self._cache_state is not None:
                return self._cache_state
            state = self._read_state_fresh()
        except OSError as exc:
            raise RuntimeError("经理机数据无法读取") from exc
        self._cache_signature = signature
        self._cache_state = state
        return state

    def _read_state_fresh(self) -> dict:
        if not self.path.exists():
            return _default_state()
        try:
            state = orjson.loads(self.path.read_bytes())
        except (OSError, orjson.JSONDecodeError) as exc:
            raise RuntimeError("经理机数据无法读取") from exc
        if state.get("schema_version") != MANAGER_STORE_SCHEMA_VERSION:
            raise RuntimeError("经理机数据版本暂不兼容")
        default = _default_state()
        for collection in default:
            if collection != "schema_version":
                state.setdefault(collection, copy.deepcopy(default[collection]))
        return state

    def _write_state(self, state: dict) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_bytes(orjson.dumps(state, option=orjson.OPT_APPEND_NEWLINE))
        for attempt in range(12):
            try:
                os.replace(temporary, self.path)
                info = self.path.stat()
                self._cache_signature = (info.st_mtime_ns, info.st_size)
                self._cache_state = state
                break
            except PermissionError:
                if attempt == 11:
                    raise
                time.sleep(0.25)

    @staticmethod
    def _check_collection(collection: str) -> None:
        if collection not in {
            "settings",
            "machines",
            "accounts",
            "account_alerts",
            "personas",
            "goals",
            "daily_plans",
            "operation_tasks",
            "agent_messages",
            "tasks",
            "schedules",
            "operation_reports",
            "transfers",
            "versions",
            "results",
            "action_attributions",
            "incremental_records",
            "evidence_downloads",
            "sync_status",
            "debug_incidents",
            "debug_releases",
        }:
            raise ValueError(f"未知经理机数据集合: {collection}")


def _project_fields(value: object, excluded: frozenset[str]) -> object:
    """Build display data while retaining the full persisted task evidence."""
    if isinstance(value, dict):
        return {k: _project_fields(v, excluded) for k, v in value.items() if k not in excluded}
    if isinstance(value, list):
        return [_project_fields(v, excluded) for v in value]
    return value


class _ManagerReadView(ManagerStore):
    """Request-local state; regular readers still return independent objects."""

    def __init__(self, root: Path, state: dict, *, copy_on_read: bool = True) -> None:
        super().__init__(root)
        self._state = state
        self._copy_on_read = copy_on_read

    def snapshot(self) -> dict:
        return super().snapshot() if self._copy_on_read else dict(self._state)

    def _read_state(self) -> dict:
        return self._state

    def _read_current_state(self) -> dict:
        return self._state

    def list(self, collection: str) -> list[dict]:
        if self._copy_on_read:
            return super().list(collection)
        self._check_collection(collection)
        return [dict(row) for row in self._state[collection].values()]

    def _transaction(self, callback: Callable[[dict], T], *, write: bool) -> T:
        if write:
            raise RuntimeError("经理机快照仅支持读取")
        return callback(self._state)
