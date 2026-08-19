"""Draft revision and one-time approval rules for V1 external output."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from capability_registry import RiskLevel, get_capability_policy
from product_store import ProductStore
from service_errors import ServiceError
from task_service import utc_now


class ApprovalService:
    def __init__(self, store: ProductStore) -> None:
        self.store = store

    def create_draft(
        self,
        *,
        account_slot: str,
        verified_uid: str,
        action_type: str,
        target_id: str,
        target_summary: str,
        content: str,
        source_event_id: str | None = None,
        task_id: str | None = None,
    ) -> dict:
        policy = self._output_policy(action_type)
        if not account_slot.strip() or not verified_uid.strip() or not target_id.strip():
            raise ServiceError("INVALID_REQUEST", "草稿必须包含账号、UID 和目标")
        if not content.strip():
            raise ServiceError("INVALID_REQUEST", "外发文本不能为空")
        draft_id = uuid.uuid4().hex
        now = utc_now()
        draft = {
            "draft_id": draft_id,
            "draft_revision_id": uuid.uuid4().hex,
            "account_slot": account_slot.strip(),
            "verified_uid": verified_uid.strip(),
            "action_type": policy.command,
            "target_id": target_id.strip(),
            "target_summary": target_summary.strip(),
            "content": content.strip(),
            "source_event_id": source_event_id,
            "task_id": task_id,
            "status": "DRAFT",
            "created_at": now,
            "updated_at": now,
        }
        return self.store.put("drafts", draft_id, draft)

    def update_draft(self, draft_id: str, **changes: str) -> dict:
        allowed = {
            "account_slot",
            "verified_uid",
            "action_type",
            "target_id",
            "target_summary",
            "content",
        }

        def mutate(state: dict) -> dict:
            draft = state["drafts"].get(draft_id)
            if draft is None:
                raise ServiceError("NOT_FOUND", "草稿不存在", 404)
            for name, value in changes.items():
                if name not in allowed:
                    raise ServiceError("INVALID_REQUEST", f"不支持修改草稿字段: {name}")
                draft[name] = value.strip()
            self._output_policy(draft["action_type"])
            if not draft["account_slot"] or not draft["verified_uid"] or not draft["target_id"]:
                raise ServiceError("INVALID_REQUEST", "草稿必须包含账号、UID 和目标")
            if not draft["content"]:
                raise ServiceError("INVALID_REQUEST", "外发文本不能为空")
            draft["draft_revision_id"] = uuid.uuid4().hex
            draft["status"] = "DRAFT"
            draft["updated_at"] = utc_now()
            for approval in state["approvals"].values():
                if approval["draft_id"] == draft_id and approval["status"] == "CONFIRMED":
                    approval["status"] = "INVALIDATED"
            return dict(draft)

        return self.store.mutate(mutate)

    def confirm(self, draft_id: str, *, ttl_seconds: int = 300) -> dict:
        if not 30 <= ttl_seconds <= 900:
            raise ServiceError("INVALID_REQUEST", "确认有效期必须在 30 到 900 秒之间")

        def mutate(state: dict) -> dict:
            draft = state["drafts"].get(draft_id)
            if draft is None:
                raise ServiceError("NOT_FOUND", "草稿不存在", 404)
            for existing in state["approvals"].values():
                if existing["draft_id"] == draft_id and existing["status"] == "CONFIRMED":
                    existing["status"] = "INVALIDATED"
            now = datetime.now(UTC)
            approval_id = uuid.uuid4().hex
            approval = {
                "approval_id": approval_id,
                "draft_id": draft_id,
                "draft_revision_id": draft["draft_revision_id"],
                "account_slot": draft["account_slot"],
                "verified_uid": draft["verified_uid"],
                "action_type": draft["action_type"],
                "target_id": draft["target_id"],
                "status": "CONFIRMED",
                "confirmed_at": now.isoformat(),
                "expires_at": (now + timedelta(seconds=ttl_seconds)).isoformat(),
                "consumed_at": None,
            }
            state["approvals"][approval_id] = approval
            draft["status"] = "CONFIRMED"
            return dict(approval)

        return self.store.mutate(mutate)

    def mark_execution(self, draft_id: str, status: str) -> dict:
        if status not in {"EXECUTED", "RESULT_UNKNOWN"}:
            raise ServiceError("INVALID_REQUEST", "未知草稿执行状态")

        def mutate(state: dict) -> dict:
            draft = state["drafts"].get(draft_id)
            if draft is None:
                raise ServiceError("NOT_FOUND", "草稿不存在", 404)
            draft["status"] = status
            draft["updated_at"] = utc_now()
            return dict(draft)

        return self.store.mutate(mutate)

    def consume(
        self,
        approval_id: str,
        *,
        account_slot: str,
        verified_uid: str,
        action_type: str,
        target_id: str,
    ) -> dict:
        def mutate(state: dict) -> dict:
            approval = state["approvals"].get(approval_id)
            if approval is None:
                raise ServiceError("NOT_FOUND", "确认不存在", 404)
            if approval["status"] == "CONSUMED":
                raise ServiceError("CONFIRMATION_CONSUMED", "该确认已经使用", 409)
            if approval["status"] != "CONFIRMED":
                raise ServiceError("DRAFT_CHANGED", "草稿已修改，需要重新确认", 409)
            if datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC):
                approval["status"] = "EXPIRED"
                raise ServiceError("CONFIRMATION_EXPIRED", "确认已经过期", 409)
            draft = state["drafts"].get(approval["draft_id"])
            if draft is None or draft["draft_revision_id"] != approval["draft_revision_id"]:
                approval["status"] = "INVALIDATED"
                raise ServiceError("DRAFT_CHANGED", "草稿已修改，需要重新确认", 409)
            expected = (
                approval["account_slot"],
                approval["verified_uid"],
                approval["action_type"],
                approval["target_id"],
            )
            actual = (account_slot, verified_uid, action_type, target_id)
            if actual != expected:
                raise ServiceError("CONFIRMATION_MISMATCH", "账号、身份、动作或目标已经变化", 409)
            approval["status"] = "CONSUMED"
            approval["consumed_at"] = utc_now()
            draft["status"] = "APPROVED_FOR_EXECUTION"
            return {"approval": dict(approval), "draft": dict(draft)}

        return self.store.mutate(mutate)

    @staticmethod
    def _output_policy(action_type: str):
        try:
            policy = get_capability_policy(action_type)
        except KeyError as exc:
            raise ServiceError("CAPABILITY_NOT_FOUND", str(exc), 404) from exc
        if not policy.enabled_in_v1 or policy.risk_level is not RiskLevel.EXTERNAL_OUTPUT:
            raise ServiceError("CAPABILITY_DISABLED", "该外发能力不属于 V1", 409)
        return policy
