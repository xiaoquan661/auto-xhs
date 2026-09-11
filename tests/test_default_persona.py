from __future__ import annotations

import json
from pathlib import Path

import pytest
from daily_operations_plan import material
from employee_codex_runner import EmployeeCodexRunner

from tests.test_employee_codex_runner import FILE_RESULT_SCRIPT, install_fake_codex, task


@pytest.mark.parametrize("persona", [None, {"display_name": "专业园艺作者", "tone": "耐心"}])
def test_employee_default_preserves_custom_persona(tmp_path: Path, monkeypatch, persona) -> None:
    install_fake_codex(monkeypatch, tmp_path, FILE_RESULT_SCRIPT)
    EmployeeCodexRunner(tmp_path)(task(parameters={"persona": persona}))
    saved = json.loads((tmp_path / "agent-runs/execution-1/task.json").read_text(encoding="utf-8"))
    if persona:
        assert saved["parameters"]["persona"] == persona
        assert not saved.get("persona_snapshot")
    else:
        assert saved["persona_snapshot"]["display_name"] == "友好评论者"


def test_daily_material_freezes_default_persona() -> None:
    result = material(
        {"plan_date": "2026-09-10", "version": 1},
        {"machine_id": "PC057", "account_slot": "monna", "metrics": {}},
        {"health_state": "READY", "current_uid": "owner"}, None,
    )
    assert result["persona_snapshot"]["display_name"] == "友好评论者"
    assert result["persona_snapshot"]["account_slot"] == "monna"
