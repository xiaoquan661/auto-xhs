"""Platform registry shared by account, task, browser, and UI layers."""

from .capabilities import (
    CapabilitySupport,
    get_platform_capability,
    list_platform_capabilities,
)
from .spec import (
    DEFAULT_PLATFORM,
    PlatformSpec,
    detect_platform,
    get_platform_spec,
    list_platform_specs,
    normalize_platform,
    rewrite_platform_url,
)

__all__ = [
    "CapabilitySupport",
    "DEFAULT_PLATFORM",
    "PlatformSpec",
    "detect_platform",
    "get_platform_capability",
    "get_platform_spec",
    "list_platform_capabilities",
    "list_platform_specs",
    "normalize_platform",
    "rewrite_platform_url",
]
