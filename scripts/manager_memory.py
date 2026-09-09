"""Manager integration for account memory and frozen task context."""

from __future__ import annotations

import copy
from typing import Any

from account_memory import AccountMemory
from manager_store import ManagerStore
from memory_context import ordered_context
from service_errors import ServiceError


class ManagerMemory:
    store: ManagerStore

    @property
    def memory(self) -> AccountMemory:
        return AccountMemory(self.store.root / "memory")

    def memory_identity(self, machine_id: str, account_slot: str) -> dict[str, Any]:
        account = self.store.get("accounts", f"{machine_id.upper()}:{account_slot}") or {}
        return {
            "machine_id": machine_id.upper(),
            "account_slot": account_slot,
            "platform": account.get("platform") or "xiaohongshu",
            "uid": account.get("uid") or "",
        }

    def memory_action(self, action: str, request: dict[str, Any]) -> dict[str, Any]:
        if action == "review-pending":
            from memory_review_service import MemoryReviewService

            return MemoryReviewService(self.memory).run()
        if action == "update":
            return self.memory.update_memory(request)
        if action == "history":
            return self.memory.history(str(request.get("memory_id") or ""))
        if action == "review":
            return self.memory.review(
                str(request.get("memory_id") or ""),
                str(request.get("decision") or ""),
                str(request.get("reviewer") or ""),
                reason=str(request.get("reason") or ""),
                related_id=str(request.get("related_id") or ""),
                expected_version=request.get("expected_version"),
                target_layer=str(request.get("target_layer") or ""),
            )
        identity = self.memory_identity(
            str(request.get("machine_id") or ""), str(request.get("account_slot") or "")
        )
        if action == "save":
            return self.memory.save({**request, "identity": identity})
        if action == "usage":
            return self.memory.usages(identity, str(request.get("task_id") or ""))
        if action == "search":
            fields = {
                k: request[k]
                for k in ("layer", "query", "task_id", "session_id", "include_inactive", "limit")
                if k in request
            }
            return self.memory.search(identity=identity, **fields)
        if action == "recall":
            return {
                "success": True,
                "context": self.memory.recall(
                    identity,
                    task_id=str(request.get("task_id") or ""),
                    session_id=str(request.get("session_id") or ""),
                    query=str(request.get("query") or ""),
                ),
            }
        raise ServiceError("INVALID_MEMORY_ACTION", "记忆操作无效")

    def attach_memory(self, request: dict[str, Any]) -> dict[str, Any]:
        previous = self.store.get("tasks", request["task_id"])
        if previous:
            frozen = previous["request"]
            request["persona_snapshot"] = copy.deepcopy(frozen.get("persona_snapshot") or {})
            context = (frozen.get("parameters") or {}).get("memory_context")
            if context:
                request["parameters"] = {**request["parameters"], "memory_context": context}
            return request
        identity = self.memory_identity(request["machine_id"], request["account_slot"])
        persona = (
            next(
                (
                    p
                    for p in self.store.list("personas")
                    if p.get("account_key") == f"{request['machine_id']}:{request['account_slot']}"
                    and p.get("status") == "ACTIVE"
                ),
                {},
            )
            or request.get("persona_snapshot")
            or {}
        )
        if persona:
            persona = {k: copy.deepcopy(v) for k, v in persona.items() if k != "versions"}
            if persona.get("identity", {}).get("uid") and persona["identity"] != identity:
                raise ServiceError(
                    "PERSONA_IDENTITY_MISMATCH", "账号身份已变更，请更新账号人格", 409
                )
            request["persona_snapshot"] = persona
        if identity["uid"]:
            query = " ".join(
                str(value)
                for value in (
                    request.get("request_summary"),
                    request["parameters"].get("keyword"),
                )
                if value
            )
            context = self.memory.recall(identity, task_id=request["task_id"], query=query)
            context = ordered_context(context, request)
            context["persona_version"] = persona.get("version", 0)
            usage = self.memory.record_usage(
                context, task_id=request["task_id"], stage="task_prepared"
            )
            context["usage_id"] = usage["usage_id"]
            request["parameters"] = {**request["parameters"], "memory_context": context}
        return request

    def ingest_memory(self, result: dict[str, Any]) -> dict[str, Any]:
        task = self.store.get("tasks", str(result.get("task_id") or "")) or {}
        request = task.get("request") or result.get("task_request") or {}
        context = (request.get("parameters") or {}).get("memory_context") or {}
        identity = context.get("identity") or result.get("memory_identity") or {}
        if not identity.get("uid"):
            return {"success": True, "inserted": 0, "duplicates": 0}
        self.memory.import_usages(identity, result.get("memory_usages") or [])
        records = list(result.get("memory_records") or [])
        session_id = str(result.get("remote_execution_id") or result.get("task_id") or "")
        if not records:
            records = [
                {
                    "layer": "runtime_fact",
                    "event_id": f"result:{result.get('result_revision', 1)}",
                    "content": str(result.get("message") or result.get("state") or "任务结束"),
                    "summary": str(result.get("state") or ""),
                    "occurred_at": result.get("completed_at") or result.get("recorded_at"),
                }
            ]
        return self.memory.intake(
            identity,
            records,
            task_id=str(result.get("task_id") or ""),
            session_id=session_id,
            source=f"worker:{result['machine_id']}",
        )
