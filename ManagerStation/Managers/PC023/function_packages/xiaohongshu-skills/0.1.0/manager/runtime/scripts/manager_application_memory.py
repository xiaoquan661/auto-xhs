"""Resolve current manager-owned memory and persona for local reply generation."""

from __future__ import annotations

from typing import Any

from account_memory import AccountMemory
from manager_store import ManagerStore


class ManagerReplyMemoryResolver:
    """Supply the manager context to an explicitly configured application service."""

    def __init__(self, store: ManagerStore) -> None:
        self.store = store
        self.memory = AccountMemory(store.root / "memory")

    def __call__(
        self,
        identity: dict[str, Any],
        task_id: str,
        query: str,
        cached: dict[str, Any],
    ) -> tuple[AccountMemory, dict[str, Any]] | None:
        if not self.memory.path.exists():
            return None
        if cached:
            if not self.store.path.exists():
                return None
            owns_account = any(
                row.get("uid") == identity["uid"]
                and row.get("platform", "xiaohongshu") == identity["platform"]
                for row in self.store.list("accounts")
            )
            if not owns_account:
                return None
        context = self.memory.recall(identity, task_id=task_id, query=query)
        persona = next(
            (
                row
                for row in self.store.list("personas")
                if row.get("identity", {}).get("uid") == identity["uid"]
                and row.get("identity", {}).get("platform") == identity["platform"]
                and row.get("status") == "ACTIVE"
            ),
            {},
        )
        context["persona"] = cached.get("persona") or {
            key: value for key, value in persona.items() if key != "versions"
        }
        return self.memory, context
