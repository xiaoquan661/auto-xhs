"""Comment and private-message reply ledger."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ReplyRecordService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def record(
        self,
        task: dict,
        *,
        draft: dict,
        approval: dict | None = None,
        source_event: dict,
        result: dict | None = None,
        operation_event_id: str = "",
    ) -> dict:
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            raise ServiceError("INVALID_REQUEST", "回复记录缺少任务 ID")
        event_type = str(source_event.get("event_type") or "")
        if event_type not in {"note_comment", "private_message"}:
            raise ServiceError("INVALID_EVENT_TYPE", "该入站事件不是评论或私信")

        result = dict(result or {})
        raw_readback = result.get("readback")
        if isinstance(raw_readback, dict):
            readback = dict(raw_readback)
        else:
            acknowledgement = str(raw_readback or "").strip()
            readback = {"acknowledgement": acknowledgement} if acknowledgement else {}
        payload = dict(source_event.get("payload") or {})
        parameters = dict(task.get("parameters") or {})
        channel = (
            "PRIVATE_MESSAGE_REPLY"
            if event_type == "private_message"
            else "COMMENT_REPLY"
        )
        now = utc_now()
        state = str(task.get("state") or "RESULT_UNKNOWN")
        readback_state = "VERIFIED" if readback else "MISSING"
        reply_record_id = uuid.uuid4().hex

        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO reply_records (
                    reply_record_id, channel, account_slot, platform, verified_uid,
                    inbound_event_id, conversation_id, thread_id, original_message_id,
                    original_content, target_user_id, target_user_name, reply_text,
                    draft_id, draft_revision, approval_id, reply_task_id,
                    operation_event_id, sent_at, result_state, error_code,
                    result_summary, readback_state, readback_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reply_task_id) DO UPDATE SET
                    approval_id = excluded.approval_id,
                    operation_event_id = excluded.operation_event_id,
                    sent_at = excluded.sent_at,
                    result_state = excluded.result_state,
                    error_code = excluded.error_code,
                    result_summary = excluded.result_summary,
                    readback_state = excluded.readback_state,
                    readback_json = excluded.readback_json,
                    updated_at = excluded.updated_at
                """,
                (
                    reply_record_id,
                    channel,
                    str(task.get("account_slot") or source_event.get("account_slot") or ""),
                    normalize_platform(task.get("platform") or source_event.get("platform")),
                    str(draft.get("verified_uid") or ""),
                    str(source_event.get("event_id") or ""),
                    str(payload.get("conversation_id") or payload.get("chat_id") or source_event.get("object_id") or ""),
                    str(payload.get("thread_id") or payload.get("feed_id") or source_event.get("object_id") or ""),
                    str(payload.get("comment_id") or payload.get("message_id") or source_event.get("platform_event_id") or ""),
                    str(parameters.get("original_content") or payload.get("content") or ""),
                    str(parameters.get("user_id") or payload.get("user_id") or source_event.get("actor_user_id") or ""),
                    str(parameters.get("nickname") or payload.get("nickname") or ""),
                    str(draft.get("content") or parameters.get("content") or ""),
                    str(draft.get("draft_id") or ""),
                    str(draft.get("draft_revision_id") or ""),
                    str((approval or {}).get("approval_id") or ""),
                    task_id,
                    str(operation_event_id or ""),
                    task.get("finished_at"),
                    state,
                    str(task.get("error_code") or ""),
                    str(task.get("result_summary") or ""),
                    readback_state,
                    json.dumps(readback, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM reply_records WHERE reply_task_id = ?",
                (task_id,),
            ).fetchone()
        return self._decode(dict(row))

    def get(self, reply_record_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM reply_records WHERE reply_record_id = ?",
            (reply_record_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "回复记录不存在", 404)
        return self._decode(row)

    def list(
        self,
        *,
        account_slot: str | None = None,
        channel: str | None = None,
        result_state: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot)
        if channel:
            clauses.append("channel = ?")
            values.append(channel)
        if result_state:
            clauses.append("result_state = ?")
            values.append(result_state)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM reply_records
            {where}
            ORDER BY COALESCE(sent_at, updated_at) DESC
            LIMIT ?
            """,
            values,
        )
        return [self._decode(row) for row in rows]

    def reconcile_result_unknown(self, reply_task_id: str, *, readback: dict) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM reply_records WHERE reply_task_id = ?",
            (reply_task_id,),
        )
        if row is None:
            return {}
        current = self._decode(row)
        merged_readback = {**dict(current.get("readback") or {}), **readback}
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE reply_records
                SET result_state = 'SUCCESS', error_code = '',
                    result_summary = '延迟回读确认私信已发送',
                    readback_state = 'VERIFIED', readback_json = ?, updated_at = ?
                WHERE reply_task_id = ? AND result_state = 'RESULT_UNKNOWN'
                """,
                (
                    json.dumps(merged_readback, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    reply_task_id,
                ),
            )
        updated = self.database.fetch_one(
            "SELECT * FROM reply_records WHERE reply_task_id = ?",
            (reply_task_id,),
        )
        return self._decode(updated) if updated else {}

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["readback"] = json.loads(decoded.pop("readback_json") or "{}")
        return decoded
