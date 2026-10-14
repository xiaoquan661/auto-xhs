"""Small local product store for V1 tasks, drafts, approvals, and events."""

from __future__ import annotations

import copy
import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from run_lock import RunLock

STORE_SCHEMA_VERSION = 1
COLLECTIONS = ("tasks", "drafts", "approvals", "events")
_PROCESS_LOCK = threading.RLock()
T = TypeVar("T")


def product_root() -> Path:
    override = os.getenv("XHS_PRODUCT_HOME")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".xhs" / "auto-xhs"


def _default_state() -> dict:
    return {
        "schema_version": STORE_SCHEMA_VERSION,
        "settings": {
            "global_paused": False,
            "global_concurrency": 3,
        },
        "tasks": {},
        "drafts": {},
        "approvals": {},
        "events": {},
    }


class ProductStore:
    """JSON-backed store with one atomic transaction boundary."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root).expanduser().resolve() if root else product_root()
        self.path = self.root / "product-state.json"
        self.lock_path = self.root / "product-state.lock"

    def snapshot(self) -> dict:
        return self._transaction(lambda state: copy.deepcopy(state), write=False)

    def get_setting(self, name: str, default=None):
        return self._transaction(
            lambda state: copy.deepcopy(state["settings"].get(name, default)),
            write=False,
        )

    def set_setting(self, name: str, value) -> dict:
        def mutate(state: dict) -> dict:
            state["settings"][name] = value
            return copy.deepcopy(state["settings"])

        return self._transaction(mutate, write=True)

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

    def mutate(self, mutator: Callable[[dict], T]) -> T:
        """Apply one multi-collection transaction."""

        return self._transaction(mutator, write=True)

    def _transaction(self, callback: Callable[[dict], T], *, write: bool) -> T:
        self.root.mkdir(parents=True, exist_ok=True)
        with _PROCESS_LOCK:
            lock = RunLock(str(self.lock_path))
            if not lock.acquire(timeout=10):
                raise TimeoutError("本地产品数据正在被其他进程使用")
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
            raise RuntimeError("本地产品数据无法读取") from exc
        if state.get("schema_version") != STORE_SCHEMA_VERSION:
            raise RuntimeError("本地产品数据版本暂不兼容")
        default = _default_state()
        state.setdefault("settings", default["settings"])
        for collection in COLLECTIONS:
            state.setdefault(collection, {})
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
        if collection not in COLLECTIONS:
            raise ValueError(f"未知产品数据集合: {collection}")
