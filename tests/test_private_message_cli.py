from __future__ import annotations

import argparse
import json

import pytest

from scripts.cli import (
    cmd_private_message_context,
    cmd_send_private_messages,
)


def _write_recipients(tmp_path):
    path = tmp_path / "recipients.json"
    path.write_text(
        json.dumps(
            [
                {
                    "user_id": "user-a",
                    "nickname": "甲",
                    "contact_purpose": "沟通露营内容合作",
                    "conversation_type": "existing",
                    "expected_peer_turn": ["可以具体聊聊"],
                    "content": "甲你好，想聊聊你的露营内容。",
                },
                {
                    "user_id": "user-b",
                    "nickname": "乙",
                    "xsec_token": "token-b",
                    "contact_purpose": "首次沟通摄影合作",
                    "conversation_type": "first_message",
                    "content": "乙你好，想聊聊你的摄影内容。",
                },
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


def test_send_cli_supports_direct_and_resumed_batches(tmp_path, monkeypatch, capsys) -> None:
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
        resume_task_id=None,
    )
    with pytest.raises(SystemExit) as direct_exit:
        cmd_send_private_messages(direct)
    assert direct_exit.value.code == 0
    capsys.readouterr()

    resumed = argparse.Namespace(
        account="alpha",
        recipients_file=None,
        resume_task_id="task-1",
    )
    with pytest.raises(SystemExit) as resumed_exit:
        cmd_send_private_messages(resumed)
    assert resumed_exit.value.code == 0

    assert [call[1] for call in calls] == [
        {
            "recipients": json.loads(recipients_file.read_text(encoding="utf-8")),
        },
        {"resume_task_id": "task-1"},
    ]
