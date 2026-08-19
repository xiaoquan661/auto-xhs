"""Local preview and one-time approval workflow for new outbound capabilities."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from operations_db import OperationsDatabase
from service_errors import ServiceError

ACTION_TYPES = {
    "send-private-message",
    "follow-user",
    "update-profile",
    "create-group",
    "invite-group-members",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ActionWorkflowService:
    def __init__(self, database: OperationsDatabase) -> None:
        self.database = database

    def create_draft(
        self,
        *,
        account_slot: str,
        verified_uid: str,
        action_type: str,
        target_id: str = "",
        payload: dict,
    ) -> dict:
        self._validate(action_type, target_id, payload)
        draft_id = uuid.uuid4().hex
        revision_id = uuid.uuid4().hex
        now = utc_now()
        preview = self._preview(action_type, target_id, payload)
        target_type = {
            "send-private-message": "user",
            "follow-user": "user",
            "update-profile": "account",
            "create-group": "new_group",
            "invite-group-members": "group",
        }[action_type]
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO action_drafts (
                    draft_id, revision_id, account_slot, verified_uid,
                    action_type, target_type, target_id, payload_json,
                    preview_json, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?)
                """,
                (
                    draft_id,
                    revision_id,
                    account_slot,
                    verified_uid,
                    action_type,
                    target_type,
                    target_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(preview, ensure_ascii=False, sort_keys=True),
                    now,
                    now,
                ),
            )
        return self.get_draft(draft_id)

    def get_draft(self, draft_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM action_drafts WHERE draft_id = ?",
            (draft_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "动作草稿不存在", 404)
        return self._decode_draft(row)

    def list_drafts(self, *, account_slot: str | None = None) -> list[dict]:
        if account_slot:
            rows = self.database.fetch_all(
                """
                SELECT * FROM action_drafts
                WHERE account_slot = ?
                ORDER BY updated_at DESC
                """,
                (account_slot,),
            )
        else:
            rows = self.database.fetch_all(
                "SELECT * FROM action_drafts ORDER BY updated_at DESC"
            )
        return [self._decode_draft(row) for row in rows]

    def update_draft(self, draft_id: str, *, target_id: str | None = None, payload=None) -> dict:
        current = self.get_draft(draft_id)
        next_target = current["target_id"] if target_id is None else target_id
        next_payload = current["payload"] if payload is None else payload
        self._validate(current["action_type"], next_target, next_payload)
        preview = self._preview(current["action_type"], next_target, next_payload)
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE action_drafts
                SET revision_id = ?, target_id = ?, payload_json = ?, preview_json = ?,
                    state = 'DRAFT', updated_at = ?
                WHERE draft_id = ?
                """,
                (
                    uuid.uuid4().hex,
                    next_target,
                    json.dumps(next_payload, ensure_ascii=False, sort_keys=True),
                    json.dumps(preview, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    draft_id,
                ),
            )
            connection.execute(
                """
                UPDATE action_approvals SET state = 'INVALIDATED'
                WHERE draft_id = ? AND state = 'CONFIRMED'
                """,
                (draft_id,),
            )
        return self.get_draft(draft_id)

    def confirm(self, draft_id: str, *, ttl_seconds: int = 300) -> dict:
        if not 30 <= ttl_seconds <= 900:
            raise ServiceError("INVALID_REQUEST", "确认有效期必须在 30 到 900 秒之间")
        draft = self.get_draft(draft_id)
        now = datetime.now(UTC)
        approval_id = uuid.uuid4().hex
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE action_approvals SET state = 'INVALIDATED'
                WHERE draft_id = ? AND state = 'CONFIRMED'
                """,
                (draft_id,),
            )
            connection.execute(
                """
                INSERT INTO action_approvals (
                    approval_id, draft_id, revision_id, state,
                    confirmed_at, expires_at, consumed_at
                ) VALUES (?, ?, ?, 'CONFIRMED', ?, ?, NULL)
                """,
                (
                    approval_id,
                    draft_id,
                    draft["revision_id"],
                    now.isoformat(),
                    (now + timedelta(seconds=ttl_seconds)).isoformat(),
                ),
            )
            connection.execute(
                "UPDATE action_drafts SET state = 'CONFIRMED', updated_at = ? WHERE draft_id = ?",
                (now.isoformat(), draft_id),
            )
        return self.get_approval(approval_id)

    def get_approval(self, approval_id: str) -> dict:
        row = self.database.fetch_one(
            "SELECT * FROM action_approvals WHERE approval_id = ?",
            (approval_id,),
        )
        if row is None:
            raise ServiceError("NOT_FOUND", "动作确认不存在", 404)
        return row

    def consume(self, approval_id: str) -> dict:
        approval = self.get_approval(approval_id)
        draft = self.get_draft(approval["draft_id"])
        if approval["state"] != "CONFIRMED":
            raise ServiceError("CONFIRMATION_CONSUMED", "该动作确认已经失效或使用", 409)
        if approval["revision_id"] != draft["revision_id"]:
            raise ServiceError("DRAFT_CHANGED", "动作草稿已修改，需要重新确认", 409)
        if datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC):
            raise ServiceError("CONFIRMATION_EXPIRED", "动作确认已经过期", 409)
        now = utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """
                UPDATE action_approvals
                SET state = 'CONSUMED', consumed_at = ?
                WHERE approval_id = ?
                """,
                (now, approval_id),
            )
            connection.execute(
                "UPDATE action_drafts SET state = 'APPROVED_FOR_EXECUTION', updated_at = ? WHERE draft_id = ?",
                (now, draft["draft_id"]),
            )
        return {"approval": self.get_approval(approval_id), "draft": self.get_draft(draft["draft_id"])}

    def mark_execution_result(self, draft_id: str, task_state: str) -> dict:
        state = {
            "SUCCESS": "EXECUTED",
            "FAILED": "FAILED",
            "BLOCKED": "BLOCKED",
            "RESULT_UNKNOWN": "RESULT_UNKNOWN",
        }.get(task_state)
        if state is None:
            raise ServiceError("INVALID_TASK_STATE", "动作任务尚未结束", 409)
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "UPDATE action_drafts SET state = ?, updated_at = ? WHERE draft_id = ?",
                (state, utc_now(), draft_id),
            )
            if cursor.rowcount != 1:
                raise ServiceError("NOT_FOUND", "动作草稿不存在", 404)
        return self.get_draft(draft_id)

    @staticmethod
    def _validate(action_type: str, target_id: str, payload: dict) -> None:
        if action_type not in ACTION_TYPES:
            raise ServiceError("INVALID_ACTION_TYPE", "不支持的动作草稿类型")
        if action_type in {"send-private-message", "follow-user", "invite-group-members"}:
            if not target_id.strip():
                raise ServiceError("INVALID_REQUEST", "动作必须包含明确目标")
        if action_type == "send-private-message" and not str(payload.get("content") or "").strip():
            raise ServiceError("INVALID_REQUEST", "私信正文不能为空")
        if action_type == "update-profile" and not (payload.get("changes") or {}):
            raise ServiceError("INVALID_REQUEST", "主页装修必须包含修改字段")
        if action_type == "create-group" and not str(payload.get("group_name") or "").strip():
            raise ServiceError("INVALID_REQUEST", "创建群聊必须填写群名")
        if action_type in {"create-group", "invite-group-members"} and not list(
            payload.get("member_user_ids") or []
        ):
            raise ServiceError("INVALID_REQUEST", "拉群必须包含明确成员")

    @staticmethod
    def _preview(action_type: str, target_id: str, payload: dict) -> dict:
        preview = {
            "action_type": action_type,
            "target_id": target_id,
            "content": payload.get("content"),
            "changes": payload.get("changes"),
            "group_name": payload.get("group_name"),
            "member_user_ids": list(payload.get("member_user_ids") or []),
        }
        if action_type == "follow-user":
            preview.update(
                {
                    "target_nickname": payload.get("target_nickname"),
                    "target_red_id": payload.get("target_red_id"),
                    "target_description": payload.get("target_description"),
                    "current_button_text": payload.get("current_button_text"),
                    "already_following": bool(payload.get("already_following")),
                }
            )
        return preview

    @staticmethod
    def _decode_draft(row: dict) -> dict:
        decoded = dict(row)
        decoded["payload"] = json.loads(decoded.pop("payload_json"))
        decoded["preview"] = json.loads(decoded.pop("preview_json"))
        return decoded
