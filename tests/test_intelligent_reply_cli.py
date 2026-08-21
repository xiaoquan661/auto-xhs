from __future__ import annotations

import argparse
import json

import pytest

from scripts.cli import build_parser, cmd_generate_reply_draft


def test_generate_reply_draft_parser_keeps_manual_review_command() -> None:
    args = build_parser().parse_args(
        [
            "--account",
            "alpha",
            "generate-reply-draft",
            "--event-id",
            "event-1",
            "--verified-uid",
            "owner-1",
        ]
    )

    assert args.func is cmd_generate_reply_draft
    assert args.reply_style == "natural"


def test_generate_reply_draft_cli_loads_optional_context_files(
    tmp_path, monkeypatch, capsys
) -> None:
    profile = tmp_path / "profile.md"
    knowledge = tmp_path / "knowledge.md"
    profile.write_text("像朋友一样简短回复。", encoding="utf-8")
    knowledge.write_text("周六下午正常开放。", encoding="utf-8")
    calls = []

    class FakeApplicationService:
        def create_intelligent_reply_draft(self, event_id, **values):
            calls.append((event_id, values))
            return {
                "success": True,
                "generation": {"reply": "可以呀，周六下午见～"},
                "draft": {"status": "DRAFT"},
            }

    monkeypatch.setattr("application_service.ApplicationService", FakeApplicationService)
    args = argparse.Namespace(
        account="alpha",
        event_id="event-1",
        verified_uid="owner-1",
        account_profile_file=str(profile),
        knowledge_file=str(knowledge),
        reply_style="natural",
    )

    with pytest.raises(SystemExit) as exited:
        cmd_generate_reply_draft(args)

    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["draft"]["status"] == "DRAFT"
    assert calls == [
        (
            "event-1",
            {
                "account_slot": "alpha",
                "verified_uid": "owner-1",
                "account_profile": "像朋友一样简短回复。",
                "knowledge": "周六下午正常开放。",
                "reply_style": "natural",
            },
        )
    ]
