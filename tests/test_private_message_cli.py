from __future__ import annotations

import argparse
import json

import pytest

from scripts.cli import (
    cmd_prepare_private_messages,
    cmd_private_message_context,
    cmd_send_private_messages,
)


def _write_recipients(tmp_path):
    path = tmp_path / "recipients.json"
    path.write_text(
        json.dumps(
            [
                {"user_id": "user-a", "nickname": "甲", "content": "甲你好，想聊聊你的露营内容。"},
                {"user_id": "user-b", "nickname": "乙", "content": "乙你好，想聊聊你的摄影内容。"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_private_message_context_cli_uses_application_service(monkeypatch, capsys) -> None:
    calls = []

    class FakeApplicationService:
        def get_private_message_context(self, account, **kwargs):
            calls.append((account, kwargs))
            return {"success": True, "conversation_type": "existing", "messages": []}

    monkeypatch.setattr("application_service.ApplicationService", FakeApplicationService)
    args = argparse.Namespace(
        account="alpha", user_id="user-a", xsec_token=None, limit=3
    )

    with pytest.raises(SystemExit) as exited:
        cmd_private_message_context(args)

    assert exited.value.code == 0
    assert json.loads(capsys.readouterr().out)["conversation_type"] == "existing"
    assert calls == [("alpha", {"user_id": "user-a", "xsec_token": "", "limit": 3})]


def test_prepare_cli_returns_one_batch_confirmation_command(tmp_path, monkeypatch, capsys) -> None:
    recipients_file = _write_recipients(tmp_path)

    class FakeApplicationService:
        def prepare_private_messages(self, account, *, recipients):
            assert account == "alpha"
            assert [item["user_id"] for item in recipients] == ["user-a", "user-b"]
            return {
                "success": True,
                "task_id": "task-1",
                "batch_revision_id": "revision-1",
                "preview": recipients,
            }

    monkeypatch.setattr("application_service.ApplicationService", FakeApplicationService)

    with pytest.raises(SystemExit) as exited:
        cmd_prepare_private_messages(
            argparse.Namespace(account="alpha", recipients_file=str(recipients_file))
        )

    assert exited.value.code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["next_command"].endswith(
        "send-private-messages --task-id task-1 --batch-revision-id revision-1 --confirm"
    )
    assert "xsec_token" not in result["next_command"]


def test_send_cli_supports_direct_and_confirmed_batches(tmp_path, monkeypatch, capsys) -> None:
    recipients_file = _write_recipients(tmp_path)
    calls = []

    class FakeApplicationService:
        def send_private_messages(self, account, **kwargs):
            calls.append((account, kwargs))
            return {"success": True, "task": {"state": "SUCCESS"}}

    monkeypatch.setattr("application_service.ApplicationService", FakeApplicationService)

    direct = argparse.Namespace(
        account="alpha",
        recipients_file=str(recipients_file),
        task_id=None,
        batch_revision_id=None,
        confirm=False,
    )
    with pytest.raises(SystemExit) as direct_exit:
        cmd_send_private_messages(direct)
    assert direct_exit.value.code == 0
    capsys.readouterr()

    confirmed = argparse.Namespace(
        account="alpha",
        recipients_file=None,
        task_id="task-1",
        batch_revision_id="revision-1",
        confirm=True,
    )
    with pytest.raises(SystemExit) as confirmed_exit:
        cmd_send_private_messages(confirmed)
    assert confirmed_exit.value.code == 0

    assert [call[1] for call in calls] == [
        {
            "recipients": json.loads(recipients_file.read_text(encoding="utf-8")),
        },
        {
            "task_id": "task-1",
            "batch_revision_id": "revision-1",
            "confirmed": True,
        },
    ]
