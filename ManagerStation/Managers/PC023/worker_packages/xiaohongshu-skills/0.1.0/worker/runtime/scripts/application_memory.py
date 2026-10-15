"""Load account memory at the point of reply generation."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from account_memory import AccountMemory
from memory_context import ordered_context, source_refs

ReplyMemoryResolver = Callable[
    [dict[str, Any], str, str, dict[str, Any]],
    tuple[AccountMemory, dict[str, Any]] | None,
]


class ApplicationMemory:
    _memory_context_resolver: ReplyMemoryResolver | None = None
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
        cached = worker.cached_context(identity)
        task_id = (cached.get("current_task") or {}).get("task_id") or event["event_id"]
        query = " ".join(
            str((event.get("payload") or {}).get(k) or "") for k in ("note_title", "content")
        )
        resolved = (
            self._memory_context_resolver(identity, task_id, query, cached)
            if self._memory_context_resolver is not None
            else None
        )
        if resolved is not None:
            memory, context = resolved
        else:
            memory = worker
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
