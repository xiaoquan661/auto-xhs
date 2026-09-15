from __future__ import annotations

import argparse
import json

import pytest

from scripts.cli import (
    build_parser,
    cmd_reply_inbox_apply,
    cmd_reply_inbox_collect,
    cmd_reply_inbox_context,
    cmd_reply_inbox_conversations,
    cmd_reply_inbox_list,
)


def test_reply_inbox_parser_exposes_composable_codex_commands() -> None:
    parser = build_parser()

    collect = parser.parse_args(["--account", "alpha", "reply-inbox", "collect"])
    listing = parser.parse_args(["--account", "alpha", "reply-inbox", "list"])
    context = parser.parse_args(
        ["--account", "alpha", "reply-inbox", "context", "--event-id", "event-1"]
    )
    apply = parser.parse_args(
        [
            "--account",
            "alpha",
            "reply-inbox",
            "apply",
            "--decision-file",
            "C:\\Temp\\reply.json",
        ]
    )
    conversations = parser.parse_args(
        ["--account", "alpha", "reply-inbox", "conversations", "--owner", "CODEX"]
    )
    assert collect.func is cmd_reply_inbox_collect
    assert listing.func is cmd_reply_inbox_list
    assert context.func is cmd_reply_inbox_context
    assert apply.func is cmd_reply_inbox_apply
    assert conversations.func is cmd_reply_inbox_conversations
    assert collect.requires_account_lock is False
    assert apply.requires_account_lock is False


def test_reply_inbox_apply_reads_structured_decision_file(tmp_path, monkeypatch, capsys) -> None:
    decision = tmp_path / "reply.json"
    decision.write_text(
        json.dumps(
            {
                "event_id": "event-1",
                "action": "skip",
                "reason": "当前消息无需继续回复",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeApplicationService:
        def apply_codex_reply_decision(self, account, values):
            calls.append((account, values))
            return {"success": True, "decision": {"decision": "skip"}}

    monkeypatch.setattr("application_service.ApplicationService", FakeApplicationService)
    args = argparse.Namespace(account="alpha", decision_file=str(decision.resolve()))

    with pytest.raises(SystemExit) as exited:
        cmd_reply_inbox_apply(args)

    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["decision"]["decision"] == "skip"
    assert calls == [
        (
            "alpha",
            {
                "event_id": "event-1",
                "action": "skip",
                "reason": "当前消息无需继续回复",
            },
        )
    ]
