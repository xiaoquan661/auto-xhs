import argparse

import cli
from capability_registry import (
    CAPABILITY_POLICIES,
    RetryPolicy,
    RiskLevel,
    get_capability_policy,
    get_operation_policy,
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


def test_service_backed_collectors_do_not_take_the_cli_lock_twice():
    parser = cli.build_parser()

    comments = parser.parse_args(["--account", "alpha", "collect-note-comments"])
    metrics = parser.parse_args(["--account", "alpha", "collect-operations-metrics"])
    follow_preview = parser.parse_args(
        [
            "--account",
            "alpha",
            "follow-user-preview",
            "--user-id",
            "user-1",
            "--xsec-token",
            "token-1",
        ]
    )
    follow = parser.parse_args(
        [
            "--account",
            "alpha",
            "follow-user",
            "--user-id",
            "user-1",
            "--xsec-token",
            "token-1",
        ]
    )
    context = parser.parse_args(
        ["--account", "alpha", "private-message-context", "--user-id", "user-1"]
    )
    prepare = parser.parse_args(
        [
            "--account",
            "alpha",
            "prepare-private-messages",
            "--recipients-file",
            "messages.json",
        ]
    )
    send = parser.parse_args(
        [
            "--account",
            "alpha",
            "send-private-messages",
            "--recipients-file",
            "messages.json",
        ]
    )

    assert comments.requires_account_lock is False
    assert metrics.requires_account_lock is False
    assert follow_preview.requires_account_lock is False
    assert follow.requires_account_lock is False
    assert context.requires_account_lock is False
    assert prepare.requires_account_lock is False
    assert send.requires_account_lock is False


def test_v1_output_confirmation_rules_include_user_authorized_random_comment():
    output_policies = [
        policy
        for policy in list_capability_policies()
        if policy.risk_level is RiskLevel.EXTERNAL_OUTPUT
    ]

    assert output_policies
    assert all(policy.retry_policy is RetryPolicy.NONE for policy in output_policies)
    assert {
        policy.command for policy in output_policies if policy.enabled_in_v1
    } == {
        "post-comment",
        "random-comment",
        "home-engagement",
        "reply-comment",
        "fill-publish",
        "fill-publish-video",
        "click-publish",
        "save-draft",
        "long-article",
        "select-template",
        "next-step",
        "send-private-messages",
    }
    assert get_capability_policy("post-comment").requires_confirmation is True
    assert get_capability_policy("reply-comment").requires_confirmation is True
    assert get_capability_policy("random-comment").requires_confirmation is False
    assert get_capability_policy("random-comment").supports_scheduling is False
    assert get_capability_policy("home-engagement").requires_confirmation is False
    assert get_capability_policy("home-engagement").requires_identity_check is True
    assert get_capability_policy("publish").enabled_in_v1 is False
    assert get_capability_policy("publish-video").enabled_in_v1 is False
    assert get_capability_policy("fill-publish").supports_scheduling is True
    assert get_capability_policy("fill-publish-video").supports_scheduling is True
    assert get_capability_policy("send-private-messages").requires_confirmation is False
    assert get_capability_policy("send-private-messages").supports_scheduling is False
    assert get_operation_policy(
        "send-private-messages", "recipient"
    ).requires_result_verification is True


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
    for command in ("like-feed", "favorite-feed", "keyword-engagement", "follow-user"):
        policy = get_capability_policy(command)
        assert policy.risk_level is RiskLevel.STATE_CHANGE
        assert policy.enabled_in_v1 is True
        assert policy.requires_identity_check is True
        assert policy.requires_result_verification is True
        assert policy.retry_policy is RetryPolicy.LIMITED
    assert get_capability_policy("follow-user").requires_confirmation is False
    assert get_capability_policy("follow-user").supports_scheduling is False
    assert get_operation_policy("follow-user", "execute").requires_confirmation is False


def test_policy_serialization_is_json_ready():
    policy = get_capability_policy("search-feeds").to_dict()

    assert policy["command"] == "search-feeds"
    assert policy["risk_level"] == "L0"
    assert policy["retry_policy"] == "safe"
    assert policy["supports_scheduling"] is True


def test_automatic_browse_is_a_read_only_schedulable_capability():
    policy = get_capability_policy("browse-feeds")

    assert policy.risk_level is RiskLevel.READ_ONLY
    assert policy.enabled_in_v1 is True
    assert policy.supports_scheduling is True
    assert policy.requires_confirmation is False


def test_account_identity_check_and_record_have_distinct_service_policies():
    public_policy = get_capability_policy("account-identity")
    check_policy = get_operation_policy("account-identity", "check")
    record_policy = get_operation_policy("account-identity", "record")

    assert public_policy.risk_level is RiskLevel.SECURITY_CONFIG
    assert check_policy.risk_level is RiskLevel.READ_ONLY
    assert check_policy.requires_confirmation is False
    assert record_policy.risk_level is RiskLevel.SECURITY_CONFIG
    assert record_policy.requires_confirmation is True


def test_unknown_service_operation_is_rejected():
    import pytest

    with pytest.raises(KeyError, match="unregistered capability operation"):
        get_operation_policy("account-identity", "overwrite")
