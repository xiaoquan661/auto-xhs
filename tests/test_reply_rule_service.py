from __future__ import annotations

from scripts.operations_db import OperationsDatabase
from scripts.reply_rule_service import ReplyRuleService


def _service(tmp_path) -> ReplyRuleService:
    return ReplyRuleService(OperationsDatabase(path=tmp_path / "operations.db"))


def test_reply_rule_is_created_disabled_and_requires_explicit_enable(tmp_path) -> None:
    service = _service(tmp_path)
    rule = service.create(
        account_slot="alpha",
        hourly_limit=3,
        daily_limit=10,
    )

    assert rule["enabled"] is False
    assert service.decision(rule["rule_id"], classification="ordinary")["authorized"] is False

    enabled = service.set_enabled(rule["rule_id"], True)
    assert enabled["enabled"] is True
    assert service.decision(rule["rule_id"], classification="ordinary")["authorized"] is True


def test_manual_category_never_uses_enabled_rule(tmp_path) -> None:
    service = _service(tmp_path)
    rule = service.create(
        account_slot="alpha",
        manual_categories=["complaint", "legal"],
    )
    service.set_enabled(rule["rule_id"], True)

    decision = service.decision(rule["rule_id"], classification="complaint")

    assert decision["authorized"] is False
    assert decision["reason"] == "manual_category"


def test_rule_fields_can_be_updated_without_enabling_it(tmp_path) -> None:
    service = _service(tmp_path)
    rule = service.create(account_slot="alpha")

    updated = service.update(
        rule["rule_id"],
        reply_style="professional",
        hourly_limit=2,
        manual_categories=["complaint"],
    )

    assert updated["enabled"] is False
    assert updated["reply_style"] == "professional"
    assert updated["hourly_limit"] == 2
    assert updated["manual_categories"] == ["complaint"]
