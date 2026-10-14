"""Read a consistent business-data snapshot without operating platform accounts."""

from __future__ import annotations

# Resolve stable imports when this canonical command is launched directly.
if __name__ == "__main__":
    import sys as _layout_sys
    from pathlib import Path as _LayoutPath

    _layout_root = next(
        parent for parent in _LayoutPath(__file__).resolve().parents
        if (parent / "source-layout.json").is_file()
    )
    _layout_sys.path.insert(0, str(_layout_root / "scripts"))
    import _role_layout as _role_layout


import base64
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from product_store import product_root


def export_business_data(root: Path | None = None) -> dict:
    root = root or product_root()
    path = root / "operations.db"
    tables = {}
    schema = {}
    if not path.exists():
        return {"success": False, "status": "MISSING_DATABASE", "source": str(path)}
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        names = [
            r[0]
            for r in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            schema[name] = [dict(row) for row in db.execute(f"PRAGMA table_info({quoted})")]
            tables[name] = [
                {
                    key: {"encoding": "base64", "value": base64.b64encode(value).decode()}
                    if isinstance(value, bytes)
                    else value
                    for key, value in dict(row).items()
                }
                for row in db.execute(f"SELECT * FROM {quoted}")
            ]
    product = root / "product-state.json"
    return {
        "success": True,
        "status": "SUCCESS",
        "source": str(path),
        "exported_at": datetime.now(UTC).isoformat(),
        "tables": tables,
        "schema": schema,
        "product_state": json.loads(product.read_text(encoding="utf-8"))
        if product.exists()
        else None,
    }


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(export_business_data(), ensure_ascii=False))
