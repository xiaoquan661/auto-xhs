import argparse

import cli
from capability_registry import (
    CAPABILITY_POLICIES,
    RetryPolicy,
    RiskLevel,
    get_capability_policy,
    list_capability_policies,
)


def _parser_commands() -> set[str]:
    parser = cli.build_parser()
    subparsers = next(
        action
        for action in parser._actions
        if isinstance(action, argparse._SubParsersAction)
    )
    return set(subparsers.choices)


def test_every_public_cli_command_has_exactly_one_policy():
    assert set(CAPABILITY_POLICIES) == _parser_commands()


def test_v1_only_enables_confirmed_comment_and_reply_outputs():
    output_policies = [
        policy
        for policy in list_capability_policies()
        if policy.risk_level is RiskLevel.EXTERNAL_OUTPUT
    ]

    assert output_policies
    assert all(policy.requires_confirmation for policy in output_policies)
    assert all(policy.retry_policy is RetryPolicy.NONE for policy in output_policies)
    assert {
        policy.command for policy in output_policies if policy.enabled_in_v1
    } == {"post-comment", "reply-comment"}
    assert get_capability_policy("publish").enabled_in_v1 is False
    assert get_capability_policy("publish-video").enabled_in_v1 is False


def test_security_configuration_always_requires_confirmation():
    security_policies = [
        policy
        for policy in list_capability_policies()
        if policy.risk_level is RiskLevel.SECURITY_CONFIG
    ]

    assert security_policies
    assert all(policy.requires_confirmation for policy in security_policies)
    assert all(policy.retry_policy is RetryPolicy.NONE for policy in security_policies)


def test_l1_social_state_changes_require_identity_and_result_verification():
    for command in ("like-feed", "favorite-feed"):
        policy = get_capability_policy(command)
        assert policy.risk_level is RiskLevel.STATE_CHANGE
        assert policy.enabled_in_v1 is True
        assert policy.requires_identity_check is True
        assert policy.requires_result_verification is True
        assert policy.retry_policy is RetryPolicy.LIMITED


def test_policy_serialization_is_json_ready():
    policy = get_capability_policy("search-feeds").to_dict()

    assert policy["command"] == "search-feeds"
    assert policy["risk_level"] == "L0"
    assert policy["retry_policy"] == "safe"
    assert policy["supports_scheduling"] is True
