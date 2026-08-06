from __future__ import annotations

from pathlib import Path

from scripts.account_autostart import task_action, task_name


def test_autostart_task_is_account_scoped_and_contains_no_credentials() -> None:
    action = task_action("brand-a")

    assert task_name("brand-a") == "auto-xhs-bridge-brand-a"
    assert "autostart.ps1" in action
    assert '-Account "brand-a"' in action
    assert str(Path("scripts")) not in action or "autostart.ps1" in action
    assert "bridge_token" not in action
    assert "account_id" not in action


def test_autostart_launcher_starts_bridge_only() -> None:
    launcher = Path(__file__).parents[1] / "scripts" / "autostart.ps1"
    content = launcher.read_text(encoding="utf-8")

    assert "account-start --bridge-only" in content
    assert "chrome.exe" not in content.lower()
