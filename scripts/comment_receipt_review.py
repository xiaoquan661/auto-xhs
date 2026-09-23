"""Review confirmed comment submissions while preserving the original receipts."""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from service_errors import ServiceError

if TYPE_CHECKING:
    from manager_store import ManagerStore


def submitted_comment(item: dict) -> bool:
    return bool(item.get("comment_id")) and item.get("platform_readback") is True and any(
        item.get(key) is True
        for key in ("submitted", "submission_confirmed", "platform_submission_confirmed")
    )


def pending_comment_attribution(result: dict) -> bool:
    return any(
        submitted_comment(item)
        and item.get("identity_match") is False
        and (item.get("attribution_review") or {}).get("status") != "CONFIRMED"
        for item in result.get("item_results") or []
        if isinstance(item, dict)
    )


def review_comments(store: ManagerStore, request: dict) -> dict:
    """Append user-confirmed account attribution revisions in one transaction."""
    machine = str(request.get("machine_id") or "")
    slot = str(request.get("account_slot") or "")
    record_ids = request.get("record_ids") or []
    reason = str(request.get("reason") or "").strip()
    if not machine or not slot or not record_ids or not reason:
        raise ServiceError("INVALID_REVIEW", "请提供机器、槽位、回执编号和核对依据")
    now = datetime.now(UTC).isoformat()

    def revise(state: dict) -> dict:
        from manager_service import ManagerService

        reviewed = []
        for record_id in dict.fromkeys(record_ids):
            original = next(
                (r for r in state["results"].values() if r.get("record_id") == record_id), None
            )
            if original is None:
                raise ServiceError("RESULT_NOT_FOUND", "待核对回执不存在", 404)
            if original.get("machine_id") != machine or original.get("account_slot") != slot:
                raise ServiceError("ACCOUNT_MISMATCH", "回执与核对的机器、槽位不一致")
            capability = original.get("capability") or (
                original.get("task_request") or {}
            ).get("capability")
            if capability != "post-comment":
                raise ServiceError("INVALID_REVIEW", "此入口用于核对已提交的评论")
            key = ManagerService._result_execution_key(original)
            latest = max(
                (r for r in state["results"].values()
                 if ManagerService._result_execution_key(r) == key),
                key=ManagerService._result_revision_key,
            )
            if not pending_comment_attribution(latest):
                if (latest.get("attribution_review") or {}).get("status") == "CONFIRMED":
                    reviewed.append(latest)
                    continue
                raise ServiceError(
                    "REVIEW_EVIDENCE_REQUIRED", "回执缺少已提交且归属待核对的评论证据"
                )
            result = copy.deepcopy(latest)
            review = {
                "status": "CONFIRMED", "source": "user_manual_review", "reason": reason,
                "machine_id": machine, "account_slot": slot, "reviewed_at": now,
            }
            for item in result["item_results"]:
                if submitted_comment(item) and item.get("identity_match") is False:
                    item.update(
                        status="SUCCESS", success=True, attribution_review=review,
                        verification_source="user_manual_review",
                        reason="平台已确认评论提交，用户核对归属本槽位",
                    )
            count = sum(
                item.get("success") is True and item.get("platform_readback") is True
                for item in result["item_results"]
            )
            target = ManagerService._result_target_count(result) or len(result["item_results"])
            revision = int(latest.get("result_revision") or latest.get("sequence") or 0) + 1
            result_key = f"{key}:{revision}"
            result.update(
                record_id=f"result:{result_key}", result_revision=revision,
                previous_record_id=latest["record_id"], attribution_review=review,
                verification_source="user_manual_review", reviewed_at=now,
                state="SUCCESS" if count >= target else "PARTIAL_SUCCESS",
                actual_count=count, completed_count=count,
                message=f"用户已核对评论归属：本任务确认提交{count}条，计入{machine}/{slot}。",
            )
            state["results"][result_key] = result
            task = state["tasks"].get(result.get("task_id"))
            if task:
                task.update(
                    state=result["state"], business_state=result["state"],
                    dispatch_state="COMPLETED", reconciliation_required=False,
                    message=result["message"], result_summary=result["message"],
                    reviewed_result_revision=revision, reviewed_record_id=result["record_id"],
                    attribution_review=review, updated_at=now,
                )
            reviewed.append(result)
        return {"success": True, "results": copy.deepcopy(reviewed)}

    return store.mutate(revise)
