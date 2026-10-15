"""Employee short-term context and memory increments over the existing outbox."""

from __future__ import annotations

import copy
import json
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from account_memory import AccountMemory, namespace, now
from memory_context import ordered_context
from service_errors import ServiceError


class WorkerMemory:
    root: Path

    @property
    def memory(self) -> AccountMemory:
        return AccountMemory(self.root / "memory")

    @staticmethod
    def execution_identity(execution: dict[str, Any]) -> dict[str, Any]:
        task = execution["task"]
        return ((task.get("parameters") or {}).get("memory_context") or {}).get("identity") or {}

    def memory_action(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        execution = self._find_execution(
            self._store.snapshot(),
            str(request.get("task_id") or ""),
            request.get("remote_execution_id"),
        )
        identity = self.execution_identity(execution)
        namespace(identity)
        task_id, session_id = execution["task_id"], execution["remote_execution_id"]
        if action == "recall":
            return {"success": True, "context": self.load_memory(execution)}
        if action == "search":
            return self.memory.search(
                identity=identity,
                layer=str(request.get("layer") or "short_term"),
                task_id=task_id,
                session_id=str(request.get("session_id") or ""),
                query=str(request.get("query") or ""),
            )
        if action != "save" or request.get("layer") not in {
            "short_term",
            "handoff",
            "account_long_term",
            "runtime_fact",
        }:
            raise ServiceError(
                "INVALID_MEMORY_ACTION", "员工记忆支持查询、恢复、观察、交接和经验建议"
            )
        source = f"worker:{execution['machine_id']}"
        if not request.get("memory_id"):
            request = {
                **request,
                "event_id": f"{source}:{session_id}:{request.get('event_id') or uuid.uuid4()}",
            }
        saved = self.memory.save(
            {
                **request,
                "identity": identity,
                "task_id": task_id,
                "session_id": session_id,
                "source": source,
            }
        )
        if request["layer"] != "short_term":
            row = saved["memory"]
            self.send_task_progress(
                task_id,
                remote_execution_id=session_id,
                stage="memory_return",
                summary=row["summary"] or "员工记忆增量已回传",
                details={"memory_records": [row]},
                event_key=f"memory:{row['memory_id']}:{row['version']}",
            )
        return saved

    def load_memory(self, execution: dict[str, Any]) -> dict[str, Any]:
        context = copy.deepcopy(
            (execution["task"].get("parameters") or {}).get("memory_context") or {}
        )
        identity = context.get("identity") or {}
        if not identity.get("uid"):
            return context
        for layer in ("governance", "shared_core", "account_long_term"):
            context[layer] = self.memory.refresh_snapshot(
                context.get(layer, []),
                identity=identity,
                layer=layer,
            )
        for layer in ("short_term", "handoff"):
            rows = self.memory.search(
                identity=identity,
                layer=layer,
                task_id=execution["task_id"] if layer == "short_term" else "",
                limit=30,
            )["memories"]
            existing = self.memory.refresh_snapshot(
                context.get(layer, []),
                identity=identity,
                layer=layer,
                task_id=execution["task_id"] if layer == "short_term" else "",
            )
            context[layer] = list({r["memory_id"]: r for r in [*existing, *rows]}.values())
        context = ordered_context(context, {**execution["task"], "task_id": execution["task_id"]})
        usage = self.memory.record_usage(
            context,
            task_id=execution["task_id"],
            session_id=execution["remote_execution_id"],
            stage="execution_context",
            usage_id=f"execution:{execution['remote_execution_id']}",
        )
        context["usage_id"] = usage["usage_id"]
        self.memory.cache_context(context)
        return context

    def result_memory(self, execution: dict[str, Any]) -> dict[str, Any]:
        identity = self.execution_identity(execution)
        if not identity.get("uid"):
            return {}
        session = execution["remote_execution_id"]
        task = execution["task"]
        expiry = (datetime.now(UTC) + timedelta(hours=168)).isoformat()
        records = [
            {
                "layer": "runtime_fact",
                "event_id": f"result:{execution['state']}",
                "content": json.dumps(
                    {
                        "state": execution["state"],
                        "message": execution.get("message", ""),
                        "item_results": execution.get("item_results") or [],
                        "evidence_index": execution.get("evidence_index") or {},
                    },
                    ensure_ascii=False,
                ),
                "occurred_at": execution.get("finished_at") or now(),
            },
            {
                "layer": "handoff",
                "event_id": f"handoff:{execution['state']}",
                "expires_at": expiry,
                "content": f"{task.get('request_summary', '')}；{execution['state']}；"
                f"{execution.get('message', '')}",
                "summary": str(execution.get("message") or execution["state"]),
                "occurred_at": execution.get("finished_at") or now(),
            },
        ]
        business = execution.get("business_result") or {}
        if isinstance(business, dict):
            records.extend(business.get("memory_records") or [])
        for message in execution.get("agent_messages") or []:
            records.extend((message.get("payload") or {}).get("memory_records") or [])
        memory_error = ""
        try:
            used_context = (task.get("parameters") or {}).get("memory_context") or {}
            self.memory.record_usage(
                used_context,
                task_id=execution["task_id"],
                session_id=session,
                stage="execution_result",
                adopted=business.get("adopted_memory_refs"),
                outcome=execution["state"],
                usage_id=f"result:{session}",
            )
            observations = self.memory.search(
                identity=identity,
                layer="short_term",
                task_id=execution["task_id"],
                session_id=session,
            )["memories"]
            if observations:
                summary = "\n".join(row["summary"] or row["content"] for row in observations)
                records[1]["content"] += "\n" + summary
                records[1]["summary"] = summary
            self.memory.intake(
                identity,
                records,
                task_id=execution["task_id"],
                session_id=session,
                source=f"worker:{execution['machine_id']}",
            )
        except (ServiceError, sqlite3.Error, OSError) as exc:
            memory_error = str(exc)
        persona = task.get("persona_snapshot") or {}
        return {
            "memory_identity": identity,
            "memory_records": records,
            "memory_status": "PENDING_REPAIR" if memory_error else "RECORDED",
            "memory_error": memory_error,
            "memory_usages": self.memory.usages(identity, execution["task_id"])["usages"]
            if not memory_error
            else [],
            "persona_id": persona.get("persona_id", ""),
            "persona_version": persona.get("version", 0),
            "persona_snapshot": copy.deepcopy(persona),
            "memory_context": copy.deepcopy(
                (task.get("parameters") or {}).get("memory_context") or {}
            ),
        }
