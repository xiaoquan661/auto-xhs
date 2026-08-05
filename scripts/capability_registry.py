"""Authoritative capability metadata shared by CLI, WebUI, and agents.

This module intentionally contains no CLI or HTTP concerns. It is the first
application-service contract introduced by the product PRD: every public
command has one policy record, and every entry point must consume the same
record instead of inferring risk from prompts or UI controls.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum


class RiskLevel(StrEnum):
    READ_ONLY = "L0"
    STATE_CHANGE = "L1"
    EXTERNAL_OUTPUT = "L2"
    SECURITY_CONFIG = "L3"


class RetryPolicy(StrEnum):
    SAFE = "safe"
    LIMITED = "limited"
    NONE = "none"


class EvidenceLevel(StrEnum):
    BASIC = "basic"
    FULL = "full"


@dataclass(frozen=True, slots=True)
class CapabilityPolicy:
    command: str
    risk_level: RiskLevel
    requires_confirmation: bool
    supports_scheduling: bool
    retry_policy: RetryPolicy
    requires_target_account: bool
    requires_identity_check: bool
    requires_result_verification: bool
    evidence_level: EvidenceLevel
    enabled_in_v1: bool

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-ready representation with stable string enums."""
        return asdict(self)


def _policy(
    command: str,
    risk_level: RiskLevel,
    *,
    supports_scheduling: bool = False,
    requires_target_account: bool = True,
    requires_identity_check: bool = False,
    requires_result_verification: bool = False,
) -> CapabilityPolicy:
    return CapabilityPolicy(
        command=command,
        risk_level=risk_level,
        requires_confirmation=risk_level
        in {RiskLevel.EXTERNAL_OUTPUT, RiskLevel.SECURITY_CONFIG},
        supports_scheduling=supports_scheduling,
        retry_policy={
            RiskLevel.READ_ONLY: RetryPolicy.SAFE,
            RiskLevel.STATE_CHANGE: RetryPolicy.LIMITED,
            RiskLevel.EXTERNAL_OUTPUT: RetryPolicy.NONE,
            RiskLevel.SECURITY_CONFIG: RetryPolicy.NONE,
        }[risk_level],
        requires_target_account=requires_target_account,
        requires_identity_check=requires_identity_check,
        requires_result_verification=requires_result_verification,
        evidence_level=(
            EvidenceLevel.FULL
            if risk_level in {RiskLevel.STATE_CHANGE, RiskLevel.EXTERNAL_OUTPUT}
            else EvidenceLevel.BASIC
        ),
        enabled_in_v1=risk_level is not RiskLevel.EXTERNAL_OUTPUT,
    )


_POLICIES = [
    # Account configuration and local lifecycle.
    _policy("account-onboard", RiskLevel.SECURITY_CONFIG, requires_target_account=False),
    _policy("account-add", RiskLevel.SECURITY_CONFIG, requires_target_account=False),
    _policy("account-import", RiskLevel.SECURITY_CONFIG, requires_target_account=False),
    _policy("account-discover", RiskLevel.READ_ONLY, requires_target_account=False),
    _policy("account-list", RiskLevel.READ_ONLY, requires_target_account=False),
    _policy("account-start", RiskLevel.STATE_CHANGE, requires_result_verification=True),
    _policy("account-status", RiskLevel.READ_ONLY),
    _policy("account-sync", RiskLevel.SECURITY_CONFIG, requires_target_account=False),
    _policy(
        "account-pair-begin",
        RiskLevel.SECURITY_CONFIG,
        requires_result_verification=True,
    ),
    _policy("account-pair-status", RiskLevel.READ_ONLY),
    _policy("account-unpair", RiskLevel.SECURITY_CONFIG, requires_result_verification=True),
    _policy(
        "account-autostart-enable",
        RiskLevel.SECURITY_CONFIG,
        requires_result_verification=True,
    ),
    _policy("account-autostart-status", RiskLevel.READ_ONLY),
    _policy(
        "account-autostart-disable",
        RiskLevel.SECURITY_CONFIG,
        requires_result_verification=True,
    ),
    _policy("account-doctor", RiskLevel.READ_ONLY, requires_target_account=False),
    _policy(
        "account-connection-enroll",
        RiskLevel.SECURITY_CONFIG,
        requires_result_verification=True,
    ),
    # account-identity can write a baseline with --record, so the public CLI
    # command takes the safer classification. The service layer will expose
    # separate read and record operations.
    _policy("account-identity", RiskLevel.SECURITY_CONFIG),
    _policy("account-switch-begin", RiskLevel.SECURITY_CONFIG),
    _policy(
        "account-switch-complete",
        RiskLevel.SECURITY_CONFIG,
        requires_result_verification=True,
    ),
    _policy("account-switch-cancel", RiskLevel.SECURITY_CONFIG),
    _policy("account-switch-history", RiskLevel.READ_ONLY),
    # Authentication.
    _policy("check-login", RiskLevel.READ_ONLY),
    _policy("login", RiskLevel.SECURITY_CONFIG, requires_result_verification=True),
    _policy("get-qrcode", RiskLevel.SECURITY_CONFIG),
    _policy("wait-login", RiskLevel.SECURITY_CONFIG, requires_result_verification=True),
    _policy("phone-login", RiskLevel.SECURITY_CONFIG, requires_result_verification=True),
    _policy("send-code", RiskLevel.SECURITY_CONFIG),
    _policy("verify-code", RiskLevel.SECURITY_CONFIG, requires_result_verification=True),
    _policy("delete-cookies", RiskLevel.SECURITY_CONFIG, requires_result_verification=True),
    # Read-only discovery and diagnostics.
    _policy("list-feeds", RiskLevel.READ_ONLY, supports_scheduling=True),
    _policy("search-feeds", RiskLevel.READ_ONLY, supports_scheduling=True),
    _policy("get-feed-detail", RiskLevel.READ_ONLY, supports_scheduling=True),
    _policy("user-profile", RiskLevel.READ_ONLY, supports_scheduling=True),
    _policy("diagnose-404", RiskLevel.READ_ONLY),
    _policy("check-risk", RiskLevel.READ_ONLY, supports_scheduling=True),
    _policy("get-netlog", RiskLevel.READ_ONLY),
    _policy("risk-report", RiskLevel.READ_ONLY, supports_scheduling=True),
    # L1 state operations allowed in v1 with limits, deduplication, and audit.
    _policy(
        "like-feed",
        RiskLevel.STATE_CHANGE,
        supports_scheduling=True,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    _policy(
        "favorite-feed",
        RiskLevel.STATE_CHANGE,
        supports_scheduling=True,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    # L2 output operations remain registered for compatibility but are
    # disabled in the first product release.
    _policy(
        "post-comment",
        RiskLevel.EXTERNAL_OUTPUT,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    _policy(
        "reply-comment",
        RiskLevel.EXTERNAL_OUTPUT,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    _policy(
        "publish",
        RiskLevel.EXTERNAL_OUTPUT,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    _policy(
        "publish-video",
        RiskLevel.EXTERNAL_OUTPUT,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    _policy("fill-publish", RiskLevel.EXTERNAL_OUTPUT, requires_identity_check=True),
    _policy(
        "fill-publish-video",
        RiskLevel.EXTERNAL_OUTPUT,
        requires_identity_check=True,
    ),
    _policy(
        "click-publish",
        RiskLevel.EXTERNAL_OUTPUT,
        requires_identity_check=True,
        requires_result_verification=True,
    ),
    _policy("save-draft", RiskLevel.EXTERNAL_OUTPUT, requires_identity_check=True),
    _policy("long-article", RiskLevel.EXTERNAL_OUTPUT, requires_identity_check=True),
    _policy("select-template", RiskLevel.EXTERNAL_OUTPUT, requires_identity_check=True),
    _policy("next-step", RiskLevel.EXTERNAL_OUTPUT, requires_identity_check=True),
]

CAPABILITY_POLICIES: dict[str, CapabilityPolicy] = {item.command: item for item in _POLICIES}

if len(CAPABILITY_POLICIES) != len(_POLICIES):
    raise RuntimeError("duplicate command in capability registry")


def get_capability_policy(command: str) -> CapabilityPolicy:
    try:
        return CAPABILITY_POLICIES[command]
    except KeyError as exc:
        raise KeyError(f"unregistered CLI capability: {command}") from exc


def list_capability_policies() -> list[CapabilityPolicy]:
    return [CAPABILITY_POLICIES[name] for name in sorted(CAPABILITY_POLICIES)]
