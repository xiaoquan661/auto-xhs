"""Persistent manager-side registry for executors, tasks, transfers, and results."""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

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

    def snapshot(self) -> dict:
        return self._transaction(lambda state: copy.deepcopy(state), write=False)

    def mutate(self, callback: Callable[[dict], T]) -> T:
        """Commit a linked plan, goal and operation task together."""
        return self._transaction(callback, write=True)

    def get(self, collection: str, item_id: str) -> dict | None:
        self._check_collection(collection)
        return self._transaction(
            lambda state: copy.deepcopy(state[collection].get(item_id)),
            write=False,
        )

    def list(self, collection: str) -> list[dict]:
        self._check_collection(collection)
        return self._transaction(
            lambda state: [copy.deepcopy(item) for item in state[collection].values()],
            write=False,
        )

    def put(self, collection: str, item_id: str, item: dict) -> dict:
        self._check_collection(collection)

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

        def mutate(state: dict) -> dict | None:
            current = state[collection].get(item_id)
            if current is None:
                return None
            updated = mutator(copy.deepcopy(current))
            state[collection][item_id] = copy.deepcopy(updated)
            return copy.deepcopy(updated)

        return self._transaction(mutate, write=True)

    def get_collection_cursor(self, machine_id: str) -> int:
        return self._transaction(
            lambda state: int(state["collection_cursors"].get(machine_id, 0)),
            write=False,
        )

    def set_collection_cursor(self, machine_id: str, sequence: int) -> int:
        def mutate(state: dict) -> int:
            current = int(state["collection_cursors"].get(machine_id, 0))
            state["collection_cursors"][machine_id] = max(current, int(sequence))
            return int(state["collection_cursors"][machine_id])

        return self._transaction(mutate, write=True)

    def record_result(self, result_key: str, result: dict) -> tuple[dict, bool]:
        """Insert one immutable remote result and report whether it was new."""

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
        self.root.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            lock = RunLock(str(self.lock_path))
            if not lock.acquire(timeout=10):
                raise TimeoutError("经理机数据正在被其他进程使用")
            try:
                state = self._read_state()
                result = callback(state)
                if write:
                    self._write_state(state)
                return result
            finally:
                lock.release()

    def _read_state(self) -> dict:
        if not self.path.exists():
            return _default_state()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
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
        temporary.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.path)

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
