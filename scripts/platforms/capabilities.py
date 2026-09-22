"""Per-platform capability availability, separate from command risk policy."""

from __future__ import annotations

from enum import StrEnum

from .spec import get_platform_spec, normalize_platform


class CapabilitySupport(StrEnum):
    SUPPORTED = "SUPPORTED"
    EXPERIMENTAL = "EXPERIMENTAL"
    UNKNOWN = "UNKNOWN"
    UNSUPPORTED = "UNSUPPORTED"


_REDNOTE_EXPERIMENTAL = {
    "account-start",
    "account-status",
    "account-identity",
    "check-login",
    "list-feeds",
    "search-feeds",
    "get-feed-detail",
    "user-profile",
    "collect-note-comments",
    "collect-private-messages",
    "collect-operations-metrics",
    "fill-publish",
    "click-publish",
    "save-draft",
}

_REDNOTE_SHARED_LOCAL = {
    "account-onboard",
    "account-add",
    "account-import",
    "account-discover",
    "account-list",
    "account-remove",
    "account-sync",
    "account-pair-begin",
    "account-pair-status",
    "account-unpair",
    "account-doctor",
    "account-connection-enroll",
    "account-switch-history",
    "generate-reply-draft",
}

_REDNOTE_UNKNOWN = {
    "browse-feeds",
    "like-feed",
    "favorite-feed",
    "keyword-engagement",
    "follow-user-preview",
    "follow-user",
    "post-comment",
    "reply-comment",
    "home-engagement",
    "private-message-context",
    "send-private-messages",
    "publish-video",
    "publish-long-article",
}


def get_platform_capability(platform: str, capability: str) -> CapabilitySupport:
    normalized = normalize_platform(platform)
    command = str(capability or "").strip()
    if normalized == "xiaohongshu":
        return CapabilitySupport.SUPPORTED
    if command in _REDNOTE_SHARED_LOCAL:
        return CapabilitySupport.SUPPORTED
    if command in _REDNOTE_EXPERIMENTAL:
        return CapabilitySupport.EXPERIMENTAL
    if command in _REDNOTE_UNKNOWN:
        return CapabilitySupport.UNKNOWN
    return CapabilitySupport.UNKNOWN


def list_platform_capabilities(platform: str, commands: list[str]) -> dict[str, str]:
    normalized = normalize_platform(platform)
    get_platform_spec(normalized)
    return {
        command: str(get_platform_capability(normalized, command))
        for command in commands
    }
