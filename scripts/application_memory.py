"""Load account memory at the point of reply generation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from account_memory import AccountMemory
from memory_context import ordered_context, source_refs


class ApplicationMemory:
    def reply_memory_context(
        self,
        event: dict[str, Any],
        verified_uid: str,
    ) -> tuple[AccountMemory, dict[str, Any]]:
        identity = {
            "account_slot": event["account_slot"],
            "uid": verified_uid,
            "platform": event.get("platform")
            or self._resolve_account_platform(event["account_slot"]),
        }
        worker = AccountMemory(
            Path(os.getenv("XHS_MANAGER_WORKER_HOME") or self._store.root / "manager-worker")
            / "memory"
        )
        manager = AccountMemory(
            Path(os.getenv("XHS_MANAGER_HOME") or self._store.root / "manager") / "memory"
        )
        cached = worker.cached_context(identity)
        task_id = (cached.get("current_task") or {}).get("task_id") or event["event_id"]
        query = " ".join(
            str((event.get("payload") or {}).get(k) or "") for k in ("note_title", "content")
        )
        manager_owns_account = False
        manager_state = manager.path.parent.parent / "manager-state.json"
        if cached and manager_state.exists():
            from manager_store import ManagerStore

            manager_owns_account = any(
                row.get("uid") == verified_uid
                and row.get("platform", "xiaohongshu") == identity["platform"]
                for row in ManagerStore(manager_state.parent).list("accounts")
            )
        memory = (
            manager if manager.path.exists() and (not cached or manager_owns_account) else worker
        )
        if memory is manager:
            context = manager.recall(identity, task_id=task_id, query=query)
            from manager_store import ManagerStore

            persona = next(
                (
                    row
                    for row in ManagerStore(manager_state.parent).list("personas")
                    if row.get("identity", {}).get("uid") == verified_uid
                    and row.get("identity", {}).get("platform") == identity["platform"]
                    and row.get("status") == "ACTIVE"
                ),
                {},
            )
            context["persona"] = cached.get("persona") or {
                k: v for k, v in persona.items() if k != "versions"
            }
        else:
            context = cached or {"identity": identity}
            for layer in (
                "governance",
                "shared_core",
                "account_long_term",
                "short_term",
                "handoff",
            ):
                context[layer] = worker.refresh_snapshot(
                    context.get(layer, []),
                    identity=identity,
                    layer=layer,
                    task_id=task_id if layer == "short_term" else "",
                )
            local = worker.recall(identity, task_id=task_id, query=query)
            for layer in ("short_term", "handoff"):
                context[layer] = list(
                    {r["memory_id"]: r for r in [*context[layer], *local[layer]]}.values()
                )[:30]
        context = ordered_context(
            context,
            {
                "task_id": task_id,
                "capability": "generate-reply",
                "parameters": {"operations_config": cached.get("operating_rules", {})},
            },
        )
        context["sources"] = source_refs(context)
        return memory, context

    def generate_reply_with_memory(
        self,
        event: dict[str, Any],
        verified_uid: str,
        **options: Any,
    ) -> dict[str, Any]:
        memory, context = self.reply_memory_context(event, verified_uid)
        result = self.reply_intelligence.generate_for_event(
            event, memory_context=context, **options
        )
        usage = memory.record_usage(
            context,
            task_id=context["current_task"]["task_id"],
            session_id=event["event_id"],
            stage="reply_generation",
            adopted=result.get("adopted_memory_refs")
            if result.get("memory_adoption_reported")
            else None,
            outcome="DRAFT_GENERATED",
        )
        result["memory_usage"] = usage
        return result
