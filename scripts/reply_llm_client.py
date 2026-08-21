"""Small OpenAI-compatible client used only for intelligent reply drafting."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass

import requests

from service_errors import ServiceError


@dataclass(frozen=True)
class ReplyModelConfig:
    api_key: str
    base_url: str
    model: str
    timeout_seconds: float = 60.0

    @classmethod
    def from_environment(cls) -> "ReplyModelConfig":
        return cls.from_sources()

    @classmethod
    def from_sources(cls, stored: dict | None = None) -> "ReplyModelConfig":
        """Load WebUI settings first and environment values as fallback."""
        saved = stored or {}
        timeout_text = str(
            saved.get("timeout_seconds")
            or os.getenv("XHS_REPLY_LLM_TIMEOUT_SECONDS", "60")
        ).strip()
        try:
            timeout_seconds = float(timeout_text)
        except ValueError as exc:
            raise ServiceError(
                "LLM_CONFIGURATION_INVALID",
                "XHS_REPLY_LLM_TIMEOUT_SECONDS 必须是数字",
            ) from exc
        return cls(
            api_key=str(
                saved.get("api_key") or os.getenv("XHS_REPLY_LLM_API_KEY", "")
            ).strip(),
            base_url=str(
                saved.get("base_url") or os.getenv("XHS_REPLY_LLM_BASE_URL", "")
            ).strip(),
            model=str(
                saved.get("model") or os.getenv("XHS_REPLY_LLM_MODEL", "")
            ).strip(),
            timeout_seconds=timeout_seconds,
        )

    def missing_fields(self) -> list[str]:
        fields = {
            "XHS_REPLY_LLM_API_KEY": self.api_key,
            "XHS_REPLY_LLM_BASE_URL": self.base_url,
            "XHS_REPLY_LLM_MODEL": self.model,
        }
        return [name for name, value in fields.items() if not value]


class OpenAICompatibleReplyClient:
    """Call one configured chat-completions endpoint and return its text."""

    def __init__(
        self,
        config: ReplyModelConfig | None = None,
        *,
        requester: Callable = requests.post,
    ) -> None:
        self.config = config or ReplyModelConfig.from_environment()
        self._requester = requester

    def status(self) -> dict:
        missing = self.config.missing_fields()
        return {
            "configured": not missing,
            "model": self.config.model,
            "base_url": self.config.base_url,
            "timeout_seconds": self.config.timeout_seconds,
            "missing": missing,
        }

    def generate(self, *, system_prompt: str, user_prompt: str) -> dict:
        missing = self.config.missing_fields()
        if missing:
            raise ServiceError(
                "LLM_NOT_CONFIGURED",
                "智能回复模型尚未配置：" + "、".join(missing),
                409,
            )

        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        try:
            response = self._requester(
                endpoint,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.65,
                },
                timeout=self.config.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
        except (requests.RequestException, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ServiceError(
                "LLM_REQUEST_FAILED",
                f"智能回复模型调用失败：{exc}",
                502,
            ) from exc

        text = str(content or "").strip()
        if not text:
            raise ServiceError("LLM_EMPTY_RESPONSE", "智能回复模型没有返回内容", 502)
        return {"content": text, "model": self.config.model}
