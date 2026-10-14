"""Layered account memory, with reviewed knowledge and replayable runtime records."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from memory_governance import MemoryGovernance, review_authority
from service_errors import ServiceError

LAYERS = {"shared_core", "account_long_term", "short_term", "handoff", "runtime_fact"}
LONG_TERM = {"shared_core", "account_long_term"}


def now() -> str:
    return datetime.now(UTC).isoformat()


def namespace(identity: dict[str, Any]) -> str:
    """A platform UID owns memory even when a browser slot is reused."""
    platform = str(identity.get("platform") or "xiaohongshu")
    uid = str(identity.get("uid") or "").strip()
    if not uid:
        raise ServiceError("MEMORY_IDENTITY_REQUIRED", "请先核验账号 UID，再使用账号记忆")
    return json.dumps([platform, uid], ensure_ascii=False, separators=(",", ":"))


class AccountMemory(MemoryGovernance):
    def __init__(self, root: str | Path) -> None:
        self.path = Path(root).resolve() / "account-memory.sqlite3"

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    memory_id TEXT PRIMARY KEY, namespace TEXT NOT NULL,
                    layer TEXT NOT NULL, state TEXT NOT NULL, task_id TEXT NOT NULL,
                    session_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    expires_at TEXT NOT NULL, updated_at TEXT NOT NULL, body TEXT NOT NULL,
                    UNIQUE(namespace, layer, event_id)
                );
                CREATE INDEX IF NOT EXISTS memory_scope
                    ON memories(namespace, layer, state, task_id, session_id);
                CREATE TABLE IF NOT EXISTS memory_versions (
                    memory_id TEXT NOT NULL, version INTEGER NOT NULL, body TEXT NOT NULL,
                    PRIMARY KEY(memory_id, version)
                );
                CREATE TABLE IF NOT EXISTS memory_usage (
                    usage_id TEXT PRIMARY KEY, namespace TEXT NOT NULL,
                    task_id TEXT NOT NULL, body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_context_cache (
                    namespace TEXT PRIMARY KEY, body TEXT NOT NULL
                );
                """
            )
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _store(connection: sqlite3.Connection, row: dict[str, Any]) -> None:
        body = json.dumps(row, ensure_ascii=False)
        connection.execute(
            "INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(memory_id) DO UPDATE SET state=excluded.state, "
            "expires_at=excluded.expires_at, updated_at=excluded.updated_at, body=excluded.body",
            [
                *(
                    row[k]
                    for k in (
                        "memory_id",
                        "namespace",
                        "layer",
                        "state",
                        "task_id",
                        "session_id",
                        "event_id",
                        "expires_at",
                        "updated_at",
                    )
                ),
                body,
            ],
        )
        connection.execute(
            "INSERT INTO memory_versions VALUES(?,?,?)",
            (row["memory_id"], row["version"], body),
        )

    def save(self, request: dict[str, Any]) -> dict[str, Any]:
        layer = str(request.get("layer") or "")
        content = str(request.get("content") or "").strip()
        source = str(request.get("source") or "").strip()
        if layer not in LAYERS or not content or not source:
            raise ServiceError("INVALID_MEMORY", "记忆需要有效层次、正文和来源")
        identity = dict(request.get("identity") or {})
        scope = "shared_core" if layer == "shared_core" else namespace(identity)
        if layer == "shared_core":
            identity = {}
        stamp = now()
        expires = ""
        if layer in {"short_term", "handoff"} or request.get("expires_at"):
            try:
                hours = float(request.get("ttl_hours", 168))
                if not 0 < hours <= 8760:
                    raise ValueError()
            except (ValueError, TypeError) as exc:
                raise ServiceError("INVALID_MEMORY_TTL", "记忆有效期应为 0 到 8760 小时") from exc
            expires = (datetime.now(UTC) + timedelta(hours=hours)).isoformat()
            if request.get("expires_at"):
                try:
                    expiry = datetime.fromisoformat(
                        str(request["expires_at"]).replace("Z", "+00:00")
                    )
                    if expiry.tzinfo is None:
                        raise ValueError()
                    expires = expiry.astimezone(UTC).isoformat()
                except ValueError as exc:
                    raise ServiceError("INVALID_MEMORY_TTL", "过期时间需要包含时区") from exc
        task_id, session_id = (str(request.get(k) or "") for k in ("task_id", "session_id"))
        if layer in {"short_term", "handoff", "runtime_fact"} and not (task_id and session_id):
            raise ServiceError("INVALID_MEMORY_CONTEXT", "运行记忆需要任务和会话编号")
        memory_id = str(request.get("memory_id") or "")
        with self.connect() as connection:
            previous = (
                connection.execute(
                    "SELECT body FROM memories WHERE memory_id=?", (memory_id,)
                ).fetchone()
                if memory_id
                else None
            )
            old = json.loads(previous[0]) if previous else None
            if memory_id and old is None:
                raise ServiceError("MEMORY_NOT_FOUND", "记忆不存在", 404)
            if old and (old["namespace"] != scope or old["layer"] != layer):
                raise ServiceError("MEMORY_SCOPE_MISMATCH", "记忆的账号身份和层次不能变更", 409)
            if old:
                if (task_id and task_id != old["task_id"]) or (
                    session_id and session_id != old["session_id"]
                ):
                    raise ServiceError("MEMORY_SCOPE_MISMATCH", "记忆所属任务和会话不能变更", 409)
                task_id, session_id = old["task_id"], old["session_id"]
            event_id = str((old or {}).get("event_id") or request.get("event_id") or uuid.uuid4())
            if old is None:
                replay = connection.execute(
                    "SELECT body FROM memories WHERE namespace=? AND layer=? AND event_id=?",
                    (scope, layer, event_id),
                ).fetchone()
                if replay:
                    previous_record = json.loads(replay[0])
                    if layer == "runtime_fact" or int(request.get("source_version") or 1) <= int(
                        previous_record.get("source_version") or 1
                    ):
                        return {
                            "success": True,
                            "status": "IDEMPOTENT",
                            "memory": previous_record,
                        }
                    old = previous_record
                    memory_id = old["memory_id"]
                    task_id, session_id = old["task_id"], old["session_id"]
            row = {
                "memory_id": memory_id or f"MEM-{uuid.uuid4().hex}",
                "namespace": scope,
                "identity": identity,
                "layer": layer,
                "category": str(
                    request.get("category") or (old or {}).get("category") or "general"
                ),
                "content": content,
                "summary": str(request.get("summary") or ""),
                "source": source,
                "task_id": task_id,
                "session_id": session_id,
                "event_id": event_id,
                "expires_at": expires,
                "state": "PENDING_REVIEW" if layer in LONG_TERM else "ACTIVE",
                "version": int((old or {}).get("version", 0)) + 1,
                "source_version": int(
                    request.get("source_version") or int((old or {}).get("source_version", 0)) + 1
                ),
                "created_at": (old or {}).get("created_at", stamp),
                "updated_at": stamp,
                "occurred_at": str(
                    request.get("occurred_at") or (old or {}).get("occurred_at") or stamp
                ),
                "reviewed_by": "",
                "reviewed_at": "",
                "effective_at": "",
                "review_status": "PENDING_REVIEW" if layer in LONG_TERM else "NOT_REQUIRED",
                "canonical_key": str(
                    request.get("canonical_key") or (old or {}).get("canonical_key") or ""
                ),
                "change": request.get("change") or ("correct" if old else "create"),
                "change_reason": str(request.get("change_reason") or ""),
            }
            row["review_authority"] = (
                "human" if (old or {}).get("review_authority") == "human" else review_authority(row)
            )
            self._store(connection, row)
        return {"success": True, "status": "SAVED", "memory": row}

    def review(
        self,
        memory_id: str,
        decision: str,
        reviewer: str,
        *,
        actor: str = "human",
        reason: str = "",
        related_id: str = "",
        expected_version: int | None = None,
        target_layer: str = "",
    ) -> dict[str, Any]:
        states = {
            "approve": "ACTIVE",
            "reject": "REJECTED",
            "retire": "RETIRED",
            "duplicate": "DUPLICATE",
            "conflict": "CONFLICT",
        }
        if decision not in states or not reviewer.strip():
            raise ServiceError("INVALID_MEMORY_REVIEW", "请填写审核人及 approve、reject 或 retire")
        with self.connect() as connection:
            value = connection.execute(
                "SELECT body FROM memories WHERE memory_id=?", (memory_id,)
            ).fetchone()
            if value is None:
                raise ServiceError("MEMORY_NOT_FOUND", "记忆不存在", 404)
            row = json.loads(value[0])
            if expected_version is not None and row["version"] != expected_version:
                raise ServiceError("MEMORY_REVIEW_STALE", "审核期间记忆已更新，请重新审核", 409)
            if row["layer"] not in LONG_TERM:
                raise ServiceError("INVALID_MEMORY_REVIEW", "运行记录通过来源回读，不参与知识审核")
            destination = target_layer or row["layer"]
            if destination not in LONG_TERM or (
                row["layer"] == "shared_core" and destination != row["layer"]
            ):
                raise ServiceError("MEMORY_REVIEW_SCOPE", "批准目标支持原账号范围或共享核心")
            review_row = dict(row)
            if destination == "shared_core":
                review_row.update(namespace="shared_core", layer="shared_core")
            decision, related_id = self._review_checks(
                connection, review_row, decision, actor, related_id
            )
            row.update(
                state=states[decision],
                reviewed_by=reviewer.strip(),
                reviewed_at=now(),
                updated_at=now(),
                version=row["version"] + 1,
                review_status="APPROVED" if decision == "approve" else states[decision],
                review_actor=actor,
                review_reason=reason,
                related_memory_id=related_id,
            )
            row["effective_at"] = row["reviewed_at"] if decision == "approve" else ""
            row["imported_at"] = row["effective_at"]
            if decision == "approve" and destination != row["layer"]:
                admitted = {
                    **row,
                    "memory_id": f"MEM-{uuid.uuid4().hex}",
                    "namespace": "shared_core",
                    "layer": "shared_core",
                    "identity": {},
                    "origin_identity": row["identity"],
                    "origin_memory_id": row["memory_id"],
                    "event_id": f"promote:{row['memory_id']}",
                    "version": 1,
                }
                row.update(state="PROMOTED", admitted_memory_id=admitted["memory_id"])
                self._store(connection, admitted)
            self._store(connection, row)
        return {"success": True, "memory": row}

    def history(self, memory_id: str) -> dict[str, Any]:
        with self.connect() as connection:
            versions = [
                json.loads(r[0])
                for r in connection.execute(
                    "SELECT body FROM memory_versions WHERE memory_id=? ORDER BY version",
                    (memory_id,),
                )
            ]
        for index, version in enumerate(versions):
            version["valid_until"] = (
                versions[index + 1]["updated_at"] if index + 1 < len(versions) else ""
            )
        return {"success": True, "versions": versions}

    def search(
        self,
        *,
        identity: dict[str, Any] | None = None,
        layer: str = "shared_core",
        query: str = "",
        task_id: str = "",
        session_id: str = "",
        include_inactive: bool = False,
        limit: int = 100,
    ) -> dict[str, Any]:
        if layer not in LAYERS:
            raise ServiceError("INVALID_MEMORY_LAYER", "记忆层次无效")
        scope = "shared_core" if layer == "shared_core" else namespace(identity or {})
        clauses, values = ["namespace=?", "layer=?"], [scope, layer]
        for key, value in (("task_id", task_id), ("session_id", session_id)):
            if value:
                clauses.append(f"{key}=?")
                values.append(value)
        if not include_inactive:
            clauses.extend(["state='ACTIVE'", "(expires_at='' OR expires_at>?)"])
            values.append(now())
        with self.connect() as connection:
            rows = [
                json.loads(r[0])
                for r in connection.execute(
                    "SELECT body FROM memories WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY updated_at DESC",
                    values,
                )
            ]
        words = query.casefold().split()
        if words:
            rows = [
                r
                for r in rows
                if any(word in (r["content"] + " " + r["summary"]).casefold() for word in words)
            ]
        return {"success": True, "memories": rows[: max(1, min(int(limit), 500))]}

    def recall(
        self, identity: dict[str, Any], *, task_id: str = "", session_id: str = "", query: str = ""
    ) -> dict[str, Any]:
        layers = {}
        for layer in ("shared_core", "account_long_term", "short_term", "handoff"):
            filters = {
                "identity": identity,
                "layer": layer,
                "task_id": task_id if layer == "short_term" else "",
                "session_id": session_id if layer == "short_term" else "",
                "limit": 30,
            }
            relevant = self.search(query=query, **filters)["memories"] if query else []
            recent = self.search(**filters)["memories"]
            layers[layer] = list({r["memory_id"]: r for r in [*relevant, *recent]}.values())[:30]
        from memory_context import ordered_context

        return ordered_context(
            {
                "identity": identity,
                "namespace": namespace(identity),
                "loaded_at": now(),
                "order": ["shared_core", "account_long_term", "short_term", "handoff"],
                **layers,
            },
            {"task_id": task_id},
        )

    def refresh_snapshot(
        self,
        records: list[dict[str, Any]],
        *,
        identity: dict[str, Any],
        layer: str,
        task_id: str = "",
    ) -> list[dict[str, Any]]:
        """Refresh locally owned snapshot rows before checking their effective lifetime."""
        if not records:
            return []
        scope = "shared_core" if layer in {"shared_core", "governance"} else namespace(identity)
        if layer == "governance":
            layer = "shared_core"
        placeholders = ",".join("?" for _ in records)
        with self.connect() as connection:
            current = {
                row["memory_id"]: {
                    **json.loads(row["body"]),
                    "state": row["state"],
                    "expires_at": row["expires_at"],
                }
                for row in connection.execute(
                    "SELECT * FROM memories WHERE namespace=? AND layer=? "
                    f"AND memory_id IN ({placeholders})",
                    [scope, layer, *(r["memory_id"] for r in records)],
                )
            }
        refreshed = [current.get(row["memory_id"], row) for row in records]
        return [
            row
            for row in refreshed
            if row.get("namespace") == scope
            and row.get("layer") == layer
            and row.get("state") == "ACTIVE"
            and (not task_id or row.get("task_id") == task_id)
            and (not row.get("expires_at") or row["expires_at"] > now())
        ]

    def intake(
        self,
        identity: dict[str, Any],
        records: list[dict[str, Any]],
        *,
        task_id: str,
        session_id: str,
        source: str,
    ) -> dict[str, Any]:
        """Natural event IDs survive repeated progress and final-result collection."""
        inserted = duplicates = 0
        for record in records:
            if not isinstance(record, dict):
                raise ServiceError("INVALID_MEMORY_RETURN", "回传记忆必须是对象")
            if record.get("layer") not in {"runtime_fact", "handoff", "account_long_term"}:
                raise ServiceError(
                    "INVALID_MEMORY_RETURN", "回传支持运行事实、交接摘要和账号经验建议"
                )
            if not record.get("event_id"):
                raise ServiceError("INVALID_MEMORY_RETURN", "回传记忆需要稳定的事件编号")
            prefix = f"{source}:{session_id}:"
            event_id = str(record["event_id"])
            result = self.save(
                {
                    **record,
                    "memory_id": "",
                    "identity": identity,
                    "task_id": task_id,
                    "session_id": session_id,
                    "source": source,
                    "source_version": int(record.get("version") or 1),
                    "event_id": event_id if event_id.startswith(prefix) else prefix + event_id,
                }
            )
            duplicates += result["status"] == "IDEMPOTENT"
            inserted += result["status"] != "IDEMPOTENT"
        return {"success": True, "inserted": inserted, "duplicates": duplicates}
