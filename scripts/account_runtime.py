"""Runtime identity helpers for user-maintained Chrome Profile sessions."""

from __future__ import annotations

from account_manager import AccountConfig


def expected_profile_directory(config: AccountConfig) -> str:
    """Return the single Chrome Profile directory expected by this slot."""
    return config.chrome_profile_directory or "Default"


def evaluate_profile_connection(config: AccountConfig, bridge_status: dict | None) -> dict:
    """Evaluate the Profile claim and, when enrolled, the paired instance proof."""
    expected_profile = expected_profile_directory(config)
    extension = (bridge_status or {}).get("extension") or {}
    extension_connected = bool(
        bridge_status and bridge_status.get("extension_connected")
    )
    reported_profile = extension.get("profile_directory")
    profile_directory_claim_matches = bool(
        extension_connected and reported_profile == expected_profile
    )
    extension_instance_enrolled = bool(
        extension_connected
        and config.extension_instance_id
        and extension.get("instance_id") == config.extension_instance_id
        and extension.get("instance_enrolled")
    )
    if extension_instance_enrolled and profile_directory_claim_matches:
        verification_level = "paired_instance"
        profile_verified = True
    elif not config.extension_instance_id and profile_directory_claim_matches:
        # Legacy per-account extensions can only repeat their configured route.
        # Preserve compatibility while making the weaker evidence explicit.
        verification_level = "legacy_claim"
        profile_verified = True
    else:
        verification_level = "unverified"
        profile_verified = False
    return {
        "bridge_running": bridge_status is not None,
        "extension_connected": extension_connected,
        "expected_profile_directory": expected_profile,
        "connected_profile_directory": reported_profile,
        "profile_directory_claim_matches": profile_directory_claim_matches,
        "profile_verified": profile_verified,
        "profile_verification_level": verification_level,
        "connection_identity_verified": bool(
            extension_connected and extension.get("identity_verified")
        ),
        "extension_instance_enrolled": extension_instance_enrolled,
    }
