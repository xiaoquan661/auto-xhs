from __future__ import annotations

import pytest
from private_message_batch import normalize_private_message_recipients
from service_errors import ServiceError


def _recipient(index: int, *, content: str | None = None) -> dict:
    return {
        "user_id": f"user-{index}",
        "nickname": f"用户{index}",
        "xsec_token": f"token-{index}",
        "contact_purpose": f"联系用户{index}沟通合作",
        "conversation_type": "existing",
        "expected_peer_turn": [],
        "content": content or f"给用户{index}的个性化文本",
    }


def test_private_message_batch_accepts_one_to_ten_personalized_recipients() -> None:
    result = normalize_private_message_recipients([_recipient(i) for i in range(10)])

    assert len(result) == 10
    assert result[0]["user_id"] == "user-0"


def test_private_message_batch_rejects_more_than_ten_recipients() -> None:
    with pytest.raises(ServiceError) as exc_info:
        normalize_private_message_recipients([_recipient(i) for i in range(11)])

    assert exc_info.value.code == "INVALID_REQUEST"


def test_private_message_batch_requires_distinct_text_for_each_recipient() -> None:
    with pytest.raises(ServiceError) as exc_info:
        normalize_private_message_recipients(
            [_recipient(1, content="同一文本"), _recipient(2, content="同一文本")]
        )

    assert exc_info.value.code == "PERSONALIZATION_REQUIRED"


def test_private_message_batch_rejects_duplicate_recipient() -> None:
    with pytest.raises(ServiceError) as exc_info:
        normalize_private_message_recipients([_recipient(1), _recipient(1)])

    assert exc_info.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize("missing_field", ["contact_purpose", "conversation_type"])
def test_private_message_batch_requires_purpose_and_context(missing_field) -> None:
    recipient = _recipient(1)
    recipient.pop(missing_field)

    with pytest.raises(ServiceError) as exc_info:
        normalize_private_message_recipients([recipient])

    assert exc_info.value.code == "INVALID_REQUEST"


def test_private_message_batch_requires_profile_token_for_first_message() -> None:
    recipient = _recipient(1)
    recipient["conversation_type"] = "first_message"
    recipient["xsec_token"] = ""

    with pytest.raises(ServiceError) as exc_info:
        normalize_private_message_recipients([recipient])

    assert exc_info.value.code == "INVALID_REQUEST"
