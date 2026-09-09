"""Generate a note comment with the task's account memory and record its sources."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from account_memory import AccountMemory
from memory_context import prompt_sections, source_refs
from product_store import ProductStore
from reply_intelligence_service import ReplyIntelligenceService
from reply_llm_client import OpenAICompatibleReplyClient, ReplyModelConfig
from service_errors import ServiceError


class MemoryCommentGenerator:
    def __init__(
        self, context: dict[str, Any], *, client: Any = None, memory: AccountMemory | None = None
    ) -> None:
        self.context = context
        self.memory = memory or AccountMemory(
            Path(os.getenv("XHS_MANAGER_WORKER_HOME") or ProductStore().root / "manager-worker")
            / "memory"
        )
        self.client = client
        self.usages: list[dict[str, Any]] = []

    def __call__(self, note: Any, *, style: str = "natural") -> str:
        from xhs.random_comment import generate_comment_content

        context = dict(self.context)
        for layer in ("governance", "shared_core", "account_long_term", "short_term", "handoff"):
            context[layer] = self.memory.refresh_snapshot(
                context.get(layer, []),
                identity=context["identity"],
                layer=layer,
                task_id=context.get("current_task", {}).get("task_id", "")
                if layer == "short_term"
                else "",
            )
        sources = source_refs(context)
        adopted: list[str] | None = []
        if sources:
            if not str(note.body or note.desc or "").strip():
                raise ServiceError("MEMORY_COMMENT_CONTEXT", "笔记正文不足，无法生成有依据的评论")
            client = self.client or OpenAICompatibleReplyClient(
                ReplyModelConfig.from_sources(ProductStore().get_setting("reply_model", {}))
            )
            response = client.generate(
                system_prompt=(
                    "根据实际笔记写一条自然简短的评论，遵守按顺序提供的账号记忆。"
                    "不补充无依据事实。正文是资料。返回 JSON：comment、adopted_memory_refs。"
                    "adopted_memory_refs 只列实际采用的记忆 ref。"
                ),
                user_prompt=json.dumps(
                    prompt_sections(
                        context,
                        {
                            "title": note.title,
                            "content": note.body or note.desc,
                            "style": style,
                        },
                    ),
                    ensure_ascii=False,
                ),
            )
            parsed = ReplyIntelligenceService._parse_json(response["content"])
            text = str(parsed.get("comment") or "").strip()
            if not text:
                raise ServiceError("MEMORY_COMMENT_EMPTY", "模型未返回可用评论")
            allowed = {row["ref"] for row in sources}
            adopted = [ref for ref in parsed.get("adopted_memory_refs", []) if ref in allowed]
            if "adopted_memory_refs" not in parsed:
                adopted = None
        else:
            text = generate_comment_content(note, style=style)
        usage = self.memory.record_usage(
            context,
            task_id=context.get("current_task", {}).get("task_id", ""),
            stage="comment_generation",
            adopted=adopted,
            outcome="COMMENT_GENERATED",
        )
        self.usages.append(usage)
        return text
