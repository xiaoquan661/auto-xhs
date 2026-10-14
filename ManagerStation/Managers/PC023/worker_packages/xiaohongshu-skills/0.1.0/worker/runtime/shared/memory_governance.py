"""Review, lifecycle and use records for account knowledge."""

from __future__ import annotations

import json
import uuid
from typing import Any

from service_errors import ServiceError

HUMAN_CATEGORIES = {
    "brand_identity",
    "expression",
    "boundaries",
    "governance",
    "persona",
    "core_identity",
    "voice_style",
    "sales_boundary",
}


def review_authority(row: dict[str, Any]) -> str:
    return "human" if row.get("category") in HUMAN_CATEGORIES else "manager_agent"


class MemoryGovernance:
    def cache_context(self, context: dict[str, Any]) -> None:
        from account_memory import namespace

        with self.connect() as connection:
            connection.execute(
                "INSERT INTO memory_context_cache VALUES(?,?) "
                "ON CONFLICT(namespace) DO UPDATE SET body=excluded.body",
                (namespace(context["identity"]), json.dumps(context, ensure_ascii=False)),
            )

    def cached_context(self, identity: dict[str, Any]) -> dict[str, Any]:
        from account_memory import namespace

        if not self.path.exists():
            return {}
        with self.connect() as connection:
            row = connection.execute(
                "SELECT body FROM memory_context_cache WHERE namespace=?", (namespace(identity),)
            ).fetchone()
        return json.loads(row[0]) if row else {}

    def import_usages(self, identity: dict[str, Any], records: list[dict[str, Any]]) -> None:
        from account_memory import namespace

        scope = namespace(identity)
        with self.connect() as connection:
            for row in records:
                if row.get("namespace") != scope:
                    continue
                connection.execute(
                    "INSERT INTO memory_usage VALUES(?,?,?,?) "
                    "ON CONFLICT(usage_id) DO UPDATE SET body=excluded.body",
                    (row["usage_id"], scope, row["task_id"], json.dumps(row, ensure_ascii=False)),
                )

    def get_memory(self, memory_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            record = connection.execute(
                "SELECT body FROM memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
        if record is None:
            raise ServiceError("MEMORY_NOT_FOUND", "记忆不存在", 404)
        return json.loads(record[0])

    def pending_reviews(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [
                json.loads(row[0])
                for row in connection.execute(
                    "SELECT body FROM memories WHERE state='PENDING_REVIEW' "
                    "ORDER BY updated_at LIMIT ?",
                    (limit,),
                )
            ]

    def _review_checks(
        self,
        connection: Any,
        row: dict[str, Any],
        decision: str,
        actor: str,
        related_id: str,
    ) -> tuple[str, str]:
        from account_memory import now

        if actor == "manager_agent" and (
            row.get("review_authority", review_authority(row)) == "human"
            or review_authority(row) == "human"
        ):
            raise ServiceError("MEMORY_HUMAN_REVIEW_REQUIRED", "核心人格和共享治理规则需要用户确认")
        if (
            decision in {"approve", "reject", "duplicate", "conflict"}
            and row["state"] != "PENDING_REVIEW"
        ):
            raise ServiceError("MEMORY_REVIEW_STATE", "请先修订记录，再审核当前版本", 409)
        peers = [
            json.loads(r[0])
            for r in connection.execute(
                "SELECT body FROM memories WHERE namespace=? AND layer=? AND state='ACTIVE' "
                "AND memory_id<>? AND (expires_at='' OR expires_at>?)",
                (row["namespace"], row["layer"], row["memory_id"], now()),
            )
        ]

        def canonical(text: str) -> str:
            return " ".join(str(text).casefold().split())

        if decision == "approve":
            exact = next(
                (p for p in peers if canonical(p["content"]) == canonical(row["content"])), None
            )
            conflict = next(
                (
                    p
                    for p in peers
                    if row.get("canonical_key") and p.get("canonical_key") == row["canonical_key"]
                ),
                None,
            )
            if exact:
                return "duplicate", exact["memory_id"]
            if conflict:
                return "conflict", conflict["memory_id"]
        if decision in {"duplicate", "conflict"} and related_id not in {
            p["memory_id"] for p in peers
        }:
            raise ServiceError(
                "MEMORY_RELATED_REQUIRED", "请选择同一记忆范围内的有效重复或冲突记录"
            )
        return decision, related_id

    def update_memory(self, request: dict[str, Any]) -> dict[str, Any]:
        from account_memory import now

        row = self.get_memory(str(request.get("memory_id") or ""))
        action = str(request.get("change") or "correct")
        reason = str(request.get("reason") or "").strip()
        if not reason or action not in {"correct", "replace", "expire"}:
            raise ServiceError("INVALID_MEMORY_UPDATE", "请选择纠正、替换或过期，并填写变更原因")
        if action == "expire":
            with self.connect() as connection:
                row = json.loads(
                    connection.execute(
                        "SELECT body FROM memories WHERE memory_id=?", (row["memory_id"],)
                    ).fetchone()[0]
                )
                row.update(
                    state="EXPIRED",
                    expires_at=now(),
                    updated_at=now(),
                    version=row["version"] + 1,
                    change=action,
                    change_reason=reason,
                )
                self._store(connection, row)
            return {"success": True, "memory": row}
        return self.save(
            {
                **row,
                **request,
                "identity": row["identity"],
                "layer": row["layer"],
                "source_version": row.get("source_version", 0) + 1,
                "change": action,
                "change_reason": reason,
            }
        )

    def record_usage(
        self,
        context: dict[str, Any],
        *,
        task_id: str,
        session_id: str = "",
        stage: str,
        adopted: list[str] | None = None,
        outcome: str = "",
        usage_id: str = "",
    ) -> dict[str, Any]:
        from account_memory import namespace, now
        from memory_context import source_refs

        sources = source_refs(context)
        allowed = {s["ref"] for s in sources}
        row = {
            "usage_id": usage_id or f"USE-{uuid.uuid4().hex}",
            "namespace": namespace(context["identity"]),
            "identity": context["identity"],
            "task_id": task_id,
            "session_id": session_id,
            "stage": stage,
            "sources": sources,
            "adopted_refs": [ref for ref in (adopted or []) if ref in allowed],
            "adoption_reported": adopted is not None,
            "outcome": outcome,
            "persona_id": context.get("persona", {}).get("persona_id", ""),
            "persona_version": context.get("persona", {}).get("version", 0),
            "recorded_at": now(),
        }
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO memory_usage VALUES(?,?,?,?) "
                "ON CONFLICT(usage_id) DO UPDATE SET body=excluded.body",
                (row["usage_id"], row["namespace"], task_id, json.dumps(row, ensure_ascii=False)),
            )
        return row

    def usages(self, identity: dict[str, Any], task_id: str = "") -> dict[str, Any]:
        from account_memory import namespace

        with self.connect() as connection:
            rows = [
                json.loads(r[0])
                for r in connection.execute(
                    "SELECT body FROM memory_usage WHERE namespace=? AND (?='' OR task_id=?) "
                    "ORDER BY rowid DESC LIMIT 200",
                    (namespace(identity), task_id, task_id),
                )
            ]
        return {"success": True, "usages": rows}
