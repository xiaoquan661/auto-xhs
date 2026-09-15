from __future__ import annotations

from scripts.operations_db import OperationsDatabase
from scripts.reply_rule_service import ReplyRuleService


def _service(tmp_path) -> ReplyRuleService:
    return ReplyRuleService(OperationsDatabase(path=tmp_path / "operations.db"))


def test_reply_rule_is_created_disabled_and_requires_explicit_enable(tmp_path) -> None:
    service = _service(tmp_path)
    rule = service.create(
        account_slot="alpha",
    )

    assert rule["enabled"] is False
    assert service.decision(rule["rule_id"])["authorized"] is False

    enabled = service.set_enabled(rule["rule_id"], True)
    assert enabled["enabled"] is True
    assert service.decision(rule["rule_id"])["authorized"] is True


def test_rule_fields_can_be_updated_without_enabling_it(tmp_path) -> None:
    service = _service(tmp_path)
    rule = service.create(account_slot="alpha")

    updated = service.update(
        rule["rule_id"],
        reply_style="professional",
    )

    assert updated["enabled"] is False
    assert updated["reply_style"] == "professional"


def test_rule_update_creates_immutable_version_snapshot(tmp_path) -> None:
    service = _service(tmp_path)
    created = service.create(account_slot="alpha", reply_style="natural")
    enabled = service.set_enabled(created["rule_id"], True)
    updated = service.update(created["rule_id"], reply_style="professional")

    assert created["rule_version"] == 1
    assert enabled["rule_version"] == 2
    assert updated["rule_version"] == 3
    assert service.get_version(created["rule_id"], 2)["reply_style"] == "natural"
    assert service.get_version(created["rule_id"], 2)["enabled"] is True
    assert [item["rule_version"] for item in service.list_versions(created["rule_id"])] == [
        3,
        2,
        1,
    ]


def test_rule_keeps_channel_switches(
    tmp_path,
) -> None:
    service = _service(tmp_path)
    rule = service.create(
        account_slot="alpha",
        comment_enabled=True,
        private_message_enabled=False,
    )
    service.set_enabled(rule["rule_id"], True)

    disabled = service.decision(
        rule["rule_id"],
        channel="private_message",
    )
    pending = service.decision(
        rule["rule_id"],
        channel="comment",
    )

    assert disabled["decision"] == "skip"
    assert disabled["reason"] == "channel_disabled"
    assert pending["decision"] == "reply"
    assert pending["reason"] == "rule_authorized"
