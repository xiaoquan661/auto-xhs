"""Local diagnostic report export with a small, explicit redaction boundary."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

SENSITIVE_FIELDS = {
    "bridge_token",
    "pairing_bundle",
    "pairing_code",
    "cookie",
    "cookies",
    "password",
    "phone",
    "verification_code",
    "xsec_token",
}


def export_diagnostic_report(
    root: str | Path,
    *,
    diagnosis: dict,
    system: dict,
    version: str,
) -> Path:
    now = datetime.now(UTC)
    directory = Path(root).expanduser().resolve() / "diagnostics"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"diagnostic-{now.strftime('%Y%m%d-%H%M%S')}.json"
    payload = redact(
        {
            "generated_at": now.isoformat(),
            "product_version": version,
            "system": system,
            "diagnosis": diagnosis,
        }
    )
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, target)
    return target


def redact(value):
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {
            key: "[已移除]" if key.lower() in SENSITIVE_FIELDS else redact(item)
            for key, item in value.items()
        }
    return value
