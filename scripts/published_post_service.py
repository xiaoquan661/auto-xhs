"""Published-post ledger backed by the local operations database."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PublishedPostService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def record(
        self,
        task: dict,
        *,
        result: dict | None = None,
        operation_event_id: str = "",
        verified_uid: str = "",
    ) -> dict:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            raise ServiceError("INVALID_REQUEST", "发布记录缺少任务 ID")

        result = dict(result or {})
        readback = dict(result.get("readback") or {})
        parameters = dict(task.get("parameters") or {})
        preview = dict(parameters.get("preview") or {})
        platform_post_id = self._first_text(
            result,
            readback,
            names=("note_id", "feed_id", "post_id", "item_id"),
        )
        stable_url = self._first_text(
            result,
            readback,
            names=("stable_url", "note_url", "post_url", "url"),
        )
        state = str(task.get("state") or "RESULT_UNKNOWN")
        published_at = self._first_text(
            result,
            readback,
            names=("published_at", "publish_time"),
        )
        if not published_at and state == "SUCCESS":
            published_at = str(task.get("finished_at") or "")
        readback_state = "VERIFIED" if state == "SUCCESS" else (
            "OBSERVED" if readback else "MISSING"
        )
        now = utc_now()
        published_post_id = uuid.uuid4().hex

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO published_posts (
                    published_post_id, publish_task_id, operation_event_id,
                    account_slot, platform, verified_uid, draft_id, draft_revision,
                    source_content_id, platform_post_id, stable_url, title,
                    content_type, published_at, result_state, readback_state,
                    error_code, result_summary, platform_result_json, readback_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(publish_task_id) DO UPDATE SET
                    operation_event_id = CASE
                        WHEN excluded.operation_event_id != '' THEN excluded.operation_event_id
                        ELSE published_posts.operation_event_id END,
                    verified_uid = CASE
                        WHEN excluded.verified_uid != '' THEN excluded.verified_uid
                        ELSE published_posts.verified_uid END,
                    platform_post_id = CASE
                        WHEN excluded.platform_post_id != '' THEN excluded.platform_post_id
                        ELSE published_posts.platform_post_id END,
                    stable_url = CASE
                        WHEN excluded.stable_url != '' THEN excluded.stable_url
                        ELSE published_posts.stable_url END,
                    published_at = COALESCE(excluded.published_at, published_posts.published_at),
                    result_state = excluded.result_state,
                    readback_state = CASE
                        WHEN excluded.readback_state != 'MISSING' THEN excluded.readback_state
                        ELSE published_posts.readback_state END,
                    error_code = excluded.error_code,
                    result_summary = excluded.result_summary,
                    platform_result_json = excluded.platform_result_json,
                    readback_json = CASE
                        WHEN excluded.readback_json != '{}' THEN excluded.readback_json
                        ELSE published_posts.readback_json END,
                    updated_at = excluded.updated_at
                """,
                (
                    published_post_id,
                    task_id,
                    str(operation_event_id or ""),
                    str(task.get("account_slot") or ""),
                    normalize_platform(task.get("platform")),
                    str(
                        verified_uid
                        or parameters.get("verified_uid")
                        or preview.get("verified_uid")
                        or ""
                    ),
                    str(preview.get("draft_id") or parameters.get("draft_id") or ""),
                    str(preview.get("draft_revision") or parameters.get("draft_revision") or ""),
                    str(preview.get("source_content_id") or parameters.get("source_content_id") or ""),
                    platform_post_id,
                    stable_url,
                    str(preview.get("title") or ""),
                    str(preview.get("kind") or ""),
                    published_at or None,
                    state,
                    readback_state,
                    str(task.get("error_code") or ""),
                    str(task.get("result_summary") or ""),
                    json.dumps(result, ensure_ascii=False, sort_keys=True),
                    json.dumps(readback, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM published_posts WHERE publish_task_id = ?",
                (task_id,),
            ).fetchone()
        return self._decode(dict(row))

    def get(self, published_post_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM published_posts WHERE published_post_id = ?",
            (published_post_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "发布记录不存在", 404)
        return self._decode(row)

    def list(
        self,
        *,
        account_slot: str | None = None,
        result_state: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot)
        if result_state:
            clauses.append("result_state = ?")
            values.append(result_state)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM published_posts
            {where}
            ORDER BY COALESCE(published_at, updated_at) DESC
            LIMIT ?
            """,
            values,
        )
        return [self._decode(row) for row in rows]

    @staticmethod
    def _first_text(*sources: dict, names: tuple[str, ...]) -> str:
        for name in names:
            for source in sources:
                value = str(source.get(name) or "").strip()
                if value:
                    return value
        return ""

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["platform_result"] = json.loads(
            decoded.pop("platform_result_json") or "{}"
        )
        decoded["readback"] = json.loads(decoded.pop("readback_json") or "{}")
        return decoded
