from __future__ import annotations

import json

from scripts.diagnostic_export import export_diagnostic_report


def test_diagnostic_export_removes_sensitive_fields(tmp_path) -> None:
    target = export_diagnostic_report(
        tmp_path,
        diagnosis={
            "healthy": False,
            "bridge_token": "secret-value",
            "nested": {"xsec_token": "page-token", "message": "offline"},
        },
        system={"global_paused": False, "cookies": "cookie-value"},
        version="test-v1",
    )

    payload = json.loads(target.read_text(encoding="utf-8"))
    raw = target.read_text(encoding="utf-8")
    assert payload["diagnosis"]["nested"]["message"] == "offline"
    assert payload["diagnosis"]["bridge_token"] == "[已移除]"
    assert "secret-value" not in raw
    assert "page-token" not in raw
    assert "cookie-value" not in raw
