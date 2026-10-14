"""Ordered knowledge context and traceable source references."""

from __future__ import annotations

import copy
from typing import Any

ORDER = ["governance", "shared_core", "account_long_term", "short_term", "current_task"]
GOVERNANCE = "遵守账号运营规则；依据实际证据表达；执行结果以回读为准。"


def source_refs(context: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "ref": f"{row['memory_id']}@{row['version']}",
            **{k: row.get(k) for k in ("memory_id", "version", "source", "layer", "namespace")},
        }
        for layer in ("governance", "shared_core", "account_long_term", "short_term", "handoff")
        for row in context.get(layer, [])
        if isinstance(row, dict) and row.get("memory_id")
    ]


def ordered_context(context: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(context)
    result["order"] = ORDER
    shared = result.get("shared_core", [])
    result["governance"] = [
        *result.get("governance", []),
        *(row for row in shared if row.get("category") == "governance"),
    ]
    result["shared_core"] = [row for row in shared if row.get("category") != "governance"]
    result["operating_rules"] = task.get("parameters", {}).get("operations_config", {})
    result["persona"] = copy.deepcopy(task.get("persona_snapshot") or context.get("persona") or {})
    result["current_task"] = {k: task.get(k) for k in ("task_id", "capability", "request_summary")}
    result["sources"] = source_refs(result)
    return result


def prompt_sections(context: dict[str, Any], current_task: dict[str, Any]) -> dict[str, Any]:
    def records(layer: str) -> list[dict[str, Any]]:
        return [
            {
                "ref": f"{row['memory_id']}@{row['version']}",
                "content": row["content"],
                "source": row["source"],
            }
            for row in context.get(layer, [])
        ]

    return {
        "governance": {
            "rules": GOVERNANCE,
            "operating_rules": context.get("operating_rules", {}),
            "account_persona": context.get("persona", {}),
            "approved_rules": records("governance"),
        },
        "shared_core": records("shared_core"),
        "account_long_term": records("account_long_term"),
        "short_term": {"observations": records("short_term"), "handoff": records("handoff")},
        "current_task": current_task,
    }
