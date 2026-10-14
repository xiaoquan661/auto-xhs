"""Static Xiaohongshu and RedNote web platform specifications."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from urllib.parse import urlparse, urlunparse

DEFAULT_PLATFORM = "xiaohongshu"


@dataclass(frozen=True, slots=True)
class PlatformSpec:
    platform_id: str
    display_name: str
    web_origin: str
    creator_origin: str
    supported_hosts: tuple[str, ...]
    cookie_domains: tuple[str, ...]
    api_hosts: tuple[str, ...]
    home_route: str
    search_route: str
    detail_route: str
    profile_route: str
    notification_route: str
    default_locale: str

    @property
    def home_url(self) -> str:
        return self.web_origin + self.home_route

    @property
    def notification_url(self) -> str:
        return self.web_origin + self.notification_route

    def public_dict(self) -> dict[str, object]:
        return asdict(self)

    def owns_hostname(self, hostname: str) -> bool:
        normalized = hostname.strip().lower().rstrip(".")
        return any(
            normalized == host or normalized.endswith("." + host)
            for host in self.supported_hosts
        )


XIAOHONGSHU = PlatformSpec(
    platform_id="xiaohongshu",
    display_name="小红书",
    web_origin="https://www.xiaohongshu.com",
    creator_origin="https://creator.xiaohongshu.com",
    supported_hosts=("xiaohongshu.com",),
    cookie_domains=("xiaohongshu.com",),
    api_hosts=("xiaohongshu.com",),
    home_route="/explore",
    search_route="/search_result",
    detail_route="/explore/{note_id}",
    profile_route="/user/profile/{user_id}",
    notification_route="/notification",
    default_locale="zh-CN",
)

REDNOTE = PlatformSpec(
    platform_id="rednote",
    display_name="RedNote",
    web_origin="https://www.rednote.com",
    creator_origin="https://creator.rednote.com",
    supported_hosts=("rednote.com",),
    cookie_domains=("rednote.com",),
    api_hosts=("rednote.com",),
    home_route="/explore",
    search_route="/search_result",
    detail_route="/discovery/item/{note_id}",
    profile_route="/user/profile/{user_id}",
    notification_route="/notification",
    default_locale="en",
)

_SPECS = {
    XIAOHONGSHU.platform_id: XIAOHONGSHU,
    REDNOTE.platform_id: REDNOTE,
}


def normalize_platform(value: str | None) -> str:
    platform = str(value or DEFAULT_PLATFORM).strip().lower()
    if platform not in _SPECS:
        supported = ", ".join(sorted(_SPECS))
        raise ValueError(f"不支持的平台 {value!r}；可选值: {supported}")
    return platform


def get_platform_spec(value: str | None = None) -> PlatformSpec:
    return _SPECS[normalize_platform(value)]


def list_platform_specs() -> list[dict[str, object]]:
    return [spec.public_dict() for spec in _SPECS.values()]


def detect_platform(url_or_hostname: str) -> str | None:
    value = str(url_or_hostname or "").strip()
    if not value:
        return None
    hostname = urlparse(value).hostname if "://" in value else value.split("/", 1)[0]
    if not hostname:
        return None
    for spec in _SPECS.values():
        if spec.owns_hostname(hostname):
            return spec.platform_id
    return None


def rewrite_platform_url(url: str, platform: str) -> str:
    """Rewrite a known platform URL to the target platform and route shape."""

    target = get_platform_spec(platform)
    parsed = urlparse(str(url or ""))
    source_id = detect_platform(parsed.hostname or "")
    if source_id is None or source_id == target.platform_id:
        return str(url)

    source = get_platform_spec(source_id)
    is_creator = (parsed.hostname or "").lower().startswith("creator.")
    target_origin = urlparse(target.creator_origin if is_creator else target.web_origin)
    path = parsed.path
    source_detail_prefix = source.detail_route.split("{note_id}", 1)[0]
    target_detail_prefix = target.detail_route.split("{note_id}", 1)[0]
    if not is_creator and path.startswith(source_detail_prefix):
        note_id = path[len(source_detail_prefix) :].split("/", 1)[0]
        if note_id:
            path = target_detail_prefix + note_id
    return urlunparse(
        (
            target_origin.scheme,
            target_origin.netloc,
            path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )
