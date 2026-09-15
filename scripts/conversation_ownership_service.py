"""Track private-message conversation ownership and reply boundaries."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from operations_db import OperationsDatabase
from platforms import normalize_platform
from service_errors import ServiceError

OWNER_CODEX = "CODEX"
OWNER_STATES = {OWNER_CODEX}
RESUME_CODEX_OUTBOUND = "CODEX_OUTBOUND"


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ConversationOwnershipService:
    """Persist one owner and one handled boundary per private-message conversation."""

    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def backfill_latest_decisions(self) -> None:
        """Seed ownership for conversations decided before this table existed."""

        rows = self.database.fetch_all(
            """
            SELECT d.decision, d.reason, d.updated_at, e.account_slot, e.platform,
                   e.object_id AS user_id, e.event_id, e.payload_json
            FROM reply_decisions d
            JOIN inbound_events e ON e.event_id = d.inbound_event_id
            WHERE d.channel = 'private_message' AND e.object_id != ''
            ORDER BY d.updated_at DESC
            """
        )
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            key = (
                str(row["account_slot"]),
                normalize_platform(row.get("platform")),
                str(row["user_id"]),
            )
            if key in seen:
                continue
            seen.add(key)
            current = self.get(*key)
            if current is not None:
                continue
            payload = json.loads(str(row.get("payload_json") or "{}"))
            message_count = int(payload.get("message_count") or 0)
            if str(row.get("reason") or "") in {"result_unknown", "send_failed"}:
                self.track_pending_outbound(
                    *key,
                    event_id=str(row.get("event_id") or ""),
                    message_count=message_count,
                    updated_at=str(row.get("updated_at") or utc_now()),
                )

    def get(
        self,
        account_slot: str,
        platform: str,
        user_id: str,
    ) -> dict | None:
        row = self.database.fetch_one(
            """
            SELECT * FROM private_message_conversations
            WHERE account_slot = ? AND platform = ? AND user_id = ?
            """,
            (account_slot.strip(), normalize_platform(platform), user_id.strip()),
        )
        return self._decode(row) if row else None

    def list(
        self,
        *,
        account_slot: str | None = None,
        owner_state: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        clauses: list[str] = []
        values: list[object] = []
        if account_slot:
            clauses.append("account_slot = ?")
            values.append(account_slot.strip())
        if owner_state:
            normalized_owner = owner_state.strip().upper()
            if normalized_owner not in OWNER_STATES:
                raise ServiceError("INVALID_OWNER_STATE", "未知私信会话所有权状态")
            clauses.append("owner_state = ?")
            values.append(normalized_owner)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(max(1, int(limit)))
        rows = self.database.fetch_all(
            f"""
            SELECT * FROM private_message_conversations
            {where}
            ORDER BY updated_at DESC LIMIT ?
            """,
            values,
        )
        return [self._decode(row) for row in rows]

    def observe(
        self,
        *,
        account_slot: str,
        platform: str,
        user_id: str,
        message_count: int,
        context: list[dict],
    ) -> dict:
        """Record the latest snapshot and reconcile delayed Agent replies."""

        account_slot = account_slot.strip()
        platform = normalize_platform(platform)
        user_id = user_id.strip()
        message_count = max(0, int(message_count))
        current = self.get(account_slot, platform, user_id)
        owner_state = str((current or {}).get("owner_state") or OWNER_CODEX)
        handled_through = int((current or {}).get("handled_through_message_count") or 0)
        handoff_event_id = str((current or {}).get("handoff_event_id") or "")
        resume_after = int((current or {}).get("resume_after_message_count") or 0)
        resume_source = str((current or {}).get("resume_source") or "")
        matched_reply_task_id = str((current or {}).get("matched_reply_task_id") or "")
        last_observed = max(
            message_count,
            int((current or {}).get("last_observed_message_count") or 0),
        )
        resumed = False
        reconciliation = None
        if handoff_event_id:
            latest_self = self._latest_self_message(message_count, context)
            if latest_self and latest_self["message_count"] > handled_through:
                handoff_event = handoff_event_id
                attribution = self._codex_outbound_attribution(
                    handoff_event_id,
                    str(latest_self["content"]),
                )
                if attribution:
                    owner_state = OWNER_CODEX
                    handled_through = int(latest_self["message_count"])
                    handoff_event_id = ""
                    resume_after = handled_through
                    resume_source = RESUME_CODEX_OUTBOUND
                    matched_reply_task_id = str((attribution or {}).get("reply_task_id") or "")
                    if attribution:
                        reconciliation = {
                            "inbound_event_id": handoff_event,
                            "reply_task_id": matched_reply_task_id,
                            "message_count": handled_through,
                            "content": str(latest_self["content"]),
                        }
                    resumed = True
        result = self._upsert(
            account_slot=account_slot,
            platform=platform,
            user_id=user_id,
            owner_state=owner_state,
            handled_through_message_count=handled_through,
            handoff_event_id=handoff_event_id,
            resume_after_message_count=resume_after,
            last_observed_message_count=last_observed,
            resume_source=resume_source,
            matched_reply_task_id=matched_reply_task_id,
            resumed=resumed,
        )
        result["reconciliation"] = reconciliation
        return result

    def track_pending_outbound(
        self,
        account_slot: str,
        platform: str,
        user_id: str,
        *,
        event_id: str,
        message_count: int,
        updated_at: str | None = None,
    ) -> dict:
        current = self.get(account_slot, platform, user_id) or {}
        boundary = max(
            int(message_count),
            int(current.get("handled_through_message_count") or 0),
        )
        return self._upsert(
            account_slot=account_slot,
            platform=platform,
            user_id=user_id,
            owner_state=OWNER_CODEX,
            handled_through_message_count=boundary,
            handoff_event_id=event_id,
            resume_after_message_count=int(current.get("resume_after_message_count") or 0),
            last_observed_message_count=max(
                boundary,
                int(current.get("last_observed_message_count") or 0),
            ),
            resume_source="",
            matched_reply_task_id="",
            updated_at=updated_at,
        )

    def mark_codex_handled(
        self,
        *,
        account_slot: str,
        platform: str,
        user_id: str,
        message_count: int,
    ) -> dict:
        current = self.get(account_slot, platform, user_id) or {}
        boundary = max(
            int(message_count),
            int(current.get("handled_through_message_count") or 0),
        )
        return self._upsert(
            account_slot=account_slot,
            platform=platform,
            user_id=user_id,
            owner_state=OWNER_CODEX,
            handled_through_message_count=boundary,
            handoff_event_id="",
            resume_after_message_count=int(current.get("resume_after_message_count") or 0),
            last_observed_message_count=max(
                boundary,
                int(current.get("last_observed_message_count") or 0),
            ),
            resume_source=str(current.get("resume_source") or ""),
            matched_reply_task_id=str(current.get("matched_reply_task_id") or ""),
        )

    def _upsert(
        self,
        *,
        account_slot: str,
        platform: str,
        user_id: str,
        owner_state: str,
        handled_through_message_count: int,
        handoff_event_id: str,
        resume_after_message_count: int,
        last_observed_message_count: int,
        resume_source: str = "",
        matched_reply_task_id: str = "",
        resumed: bool = False,
        updated_at: str | None = None,
    ) -> dict:
        account_slot = account_slot.strip()
        platform = normalize_platform(platform)
        user_id = user_id.strip()
        if not account_slot or not user_id:
            raise ServiceError("INVALID_CONVERSATION", "私信会话缺少账号或联系人")
        if owner_state not in OWNER_STATES:
            raise ServiceError("INVALID_OWNER_STATE", "未知私信会话所有权状态")
        now = updated_at or utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO private_message_conversations (
                    account_slot, platform, user_id, owner_state,
                    handled_through_message_count, handoff_event_id,
                    resume_after_message_count, last_observed_message_count,
                    updated_at, resume_source, matched_reply_task_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_slot, platform, user_id) DO UPDATE SET
                    owner_state = excluded.owner_state,
                    handled_through_message_count = excluded.handled_through_message_count,
                    handoff_event_id = excluded.handoff_event_id,
                    resume_after_message_count = excluded.resume_after_message_count,
                    last_observed_message_count = excluded.last_observed_message_count,
                    updated_at = excluded.updated_at,
                    resume_source = excluded.resume_source,
                    matched_reply_task_id = excluded.matched_reply_task_id
                """,
                (
                    account_slot,
                    platform,
                    user_id,
                    owner_state,
                    max(0, int(handled_through_message_count)),
                    handoff_event_id.strip(),
                    max(0, int(resume_after_message_count)),
                    max(0, int(last_observed_message_count)),
                    now,
                    resume_source.strip(),
                    matched_reply_task_id.strip(),
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM private_message_conversations
                WHERE account_slot = ? AND platform = ? AND user_id = ?
                """,
                (account_slot, platform, user_id),
            ).fetchone()
        result = self._decode(dict(row))
        result["resumed"] = resumed
        return result

    @staticmethod
    def _latest_self_message(message_count: int, context: list[dict]) -> dict | None:
        start = max(1, int(message_count) - len(context) + 1)
        latest = None
        for offset, item in enumerate(context):
            if str(item.get("role") or "") == "self":
                latest = {
                    "message_count": start + offset,
                    "content": str(item.get("content") or "").strip(),
                }
        return latest

    def _codex_outbound_attribution(
        self,
        handoff_event_id: str,
        content: str,
    ) -> dict | None:
        if not handoff_event_id or not content:
            return None
        row = self.database.fetch_one(
            """
            SELECT reply_task_id, reply_text, result_state
            FROM reply_decisions
            WHERE inbound_event_id = ?
            """,
            (handoff_event_id,),
        )
        if (
            row
            and str(row.get("result_state") or "") == "RESULT_UNKNOWN"
            and str(row.get("reply_task_id") or "")
            and str(row.get("reply_text") or "").strip() == content.strip()
        ):
            return dict(row)
        return None

    @staticmethod
    def _decode(row: dict) -> dict:
        decoded = dict(row)
        decoded["handled_through_message_count"] = int(
            decoded.get("handled_through_message_count") or 0
        )
        decoded["resume_after_message_count"] = int(decoded.get("resume_after_message_count") or 0)
        decoded["last_observed_message_count"] = int(
            decoded.get("last_observed_message_count") or 0
        )
        return decoded
