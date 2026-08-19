from __future__ import annotations

import pytest

from scripts.action_workflow_service import ActionWorkflowService
from scripts.operations_db import OperationsDatabase
from service_errors import ServiceError


def _service(tmp_path) -> ActionWorkflowService:
    return ActionWorkflowService(OperationsDatabase(path=tmp_path / "operations.db"))


@pytest.mark.parametrize(
    ("action_type", "target_id", "payload"),
    [
        ("send-private-message", "user-1", {"content": "你好"}),
        ("follow-user", "user-1", {"reason": "内容相关"}),
        ("update-profile", "", {"changes": {"description": "新的简介"}}),
        (
            "create-group",
            "",
            {"group_name": "学习群", "member_user_ids": ["user-1", "user-2"]},
        ),
        (
            "invite-group-members",
            "group-1",
            {"member_user_ids": ["user-3"]},
        ),
    ],
)
def test_all_confirmed_action_types_create_preview(
    tmp_path,
    action_type,
    target_id,
    payload,
) -> None:
    service = _service(tmp_path)

    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="owner-1",
        action_type=action_type,
        target_id=target_id,
        payload=payload,
    )

    assert draft["state"] == "DRAFT"
    assert draft["preview"]["action_type"] == action_type
    assert draft["payload"] == payload


def test_confirmation_is_one_time_and_draft_change_invalidates_it(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="owner-1",
        action_type="send-private-message",
        target_id="user-1",
        payload={"content": "第一版"},
    )
    approval = service.confirm(draft["draft_id"])

    service.update_draft(draft["draft_id"], payload={"content": "第二版"})

    with pytest.raises(ServiceError) as changed:
        service.consume(approval["approval_id"])
    assert changed.value.code == "CONFIRMATION_CONSUMED"

    current = service.confirm(draft["draft_id"])
    consumed = service.consume(current["approval_id"])
    assert consumed["draft"]["state"] == "APPROVED_FOR_EXECUTION"

    with pytest.raises(ServiceError) as repeated:
        service.consume(current["approval_id"])
    assert repeated.value.code == "CONFIRMATION_CONSUMED"


def test_follow_preview_and_execution_state_are_persisted(tmp_path) -> None:
    service = _service(tmp_path)
    draft = service.create_draft(
        account_slot="alpha",
        verified_uid="owner-1",
        action_type="follow-user",
        target_id="user-1",
        payload={
            "target_nickname": "目标博主",
            "target_red_id": "red-1",
            "target_description": "主页简介",
            "current_button_text": "关注",
            "already_following": False,
        },
    )

    assert draft["preview"]["target_nickname"] == "目标博主"
    assert draft["preview"]["already_following"] is False
    assert service.mark_execution_result(draft["draft_id"], "SUCCESS")["state"] == "EXECUTED"
