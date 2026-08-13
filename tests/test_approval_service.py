from __future__ import annotations

import pytest
from service_errors import ServiceError

from scripts.approval_service import ApprovalService
from scripts.product_store import ProductStore


def _service(tmp_path) -> ApprovalService:
    return ApprovalService(ProductStore(tmp_path / "product"))


def _draft(service: ApprovalService) -> dict:
    return service.create_draft(
        account_slot="alpha",
        verified_uid="uid-alpha",
        action_type="post-comment",
        target_id="feed-1",
        target_summary="露营笔记",
        content="很实用的分享",
    )


def test_draft_only_allows_v1_comment_and_reply(tmp_path) -> None:
    service = _service(tmp_path)

    assert _draft(service)["action_type"] == "post-comment"
    with pytest.raises(ServiceError) as exc_info:
        service.create_draft(
            account_slot="alpha",
            verified_uid="uid-alpha",
            action_type="publish",
            target_id="publisher",
            target_summary="发布",
            content="内容",
        )
    assert exc_info.value.code == "CAPABILITY_DISABLED"


def test_editing_draft_invalidates_old_confirmation(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _draft(service)
    approval = service.confirm(draft["draft_id"])

    updated = service.update_draft(draft["draft_id"], content="修改后的文本")

    assert updated["draft_revision_id"] != draft["draft_revision_id"]
    with pytest.raises(ServiceError) as exc_info:
        service.consume(
            approval["approval_id"],
            account_slot="alpha",
            verified_uid="uid-alpha",
            action_type="post-comment",
            target_id="feed-1",
        )
    assert exc_info.value.code == "DRAFT_CHANGED"


def test_confirmation_matches_context_and_is_consumed_once(tmp_path) -> None:
    service = _service(tmp_path)
    draft = _draft(service)
    approval = service.confirm(draft["draft_id"])

    with pytest.raises(ServiceError) as mismatch:
        service.consume(
            approval["approval_id"],
            account_slot="beta",
            verified_uid="uid-alpha",
            action_type="post-comment",
            target_id="feed-1",
        )
    assert mismatch.value.code == "CONFIRMATION_MISMATCH"

    result = service.consume(
        approval["approval_id"],
        account_slot="alpha",
        verified_uid="uid-alpha",
        action_type="post-comment",
        target_id="feed-1",
    )
    assert result["approval"]["status"] == "CONSUMED"

    with pytest.raises(ServiceError) as consumed:
        service.consume(
            approval["approval_id"],
            account_slot="alpha",
            verified_uid="uid-alpha",
            action_type="post-comment",
            target_id="feed-1",
        )
    assert consumed.value.code == "CONFIRMATION_CONSUMED"


def test_expired_confirmation_is_rejected(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    draft = _draft(service)
    approval = service.confirm(draft["draft_id"], ttl_seconds=30)

    def expire(state):
        state["approvals"][approval["approval_id"]]["expires_at"] = (
            "2020-01-01T00:00:00+00:00"
        )

    service.store.mutate(expire)
    with pytest.raises(ServiceError) as exc_info:
        service.consume(
            approval["approval_id"],
            account_slot="alpha",
            verified_uid="uid-alpha",
            action_type="post-comment",
            target_id="feed-1",
        )
    assert exc_info.value.code == "CONFIRMATION_EXPIRED"
