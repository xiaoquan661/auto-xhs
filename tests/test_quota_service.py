from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from service_errors import ServiceError

from scripts.product_store import ProductStore
from scripts.quota_service import QuotaService


def _event(store, *, state="SUCCESS", target="feed-1") -> None:
    event_id = uuid.uuid4().hex
    store.put(
        "events",
        event_id,
        {
            "event_id": event_id,
            "account_slot": "alpha",
            "capability": "like-feed",
            "risk_level": "L1",
            "target_key": target,
            "state": state,
            "finished_at": datetime.now(UTC).isoformat(),
        },
    )


def test_quota_rejects_duplicate_target(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    service = QuotaService(store)
    _event(store)

    with pytest.raises(ServiceError) as exc_info:
        service.check_l1(account="alpha", capability="like-feed", target_key="feed-1")
    assert exc_info.value.code == "DUPLICATE_ACTION"


def test_quota_rejects_hourly_limit(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    store.set_setting(
        "l1_limits",
        {"hourly": 1, "daily": 100, "dedup_minutes": 10, "failure_threshold": 3},
    )
    service = QuotaService(store)
    _event(store, target="first")

    with pytest.raises(ServiceError) as exc_info:
        service.check_l1(account="alpha", capability="like-feed", target_key="second")
    assert exc_info.value.code == "RATE_LIMITED"


def test_quota_opens_circuit_after_repeated_failures(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    service = QuotaService(store)
    for index in range(3):
        _event(store, state="RESULT_UNKNOWN", target=f"feed-{index}")

    with pytest.raises(ServiceError) as exc_info:
        service.check_l1(account="alpha", capability="like-feed", target_key="new")
    assert exc_info.value.code == "RISK_BLOCKED"


def test_batch_quota_counts_each_successful_keyword_action(tmp_path) -> None:
    store = ProductStore(tmp_path / "product")
    store.set_setting(
        "l1_limits",
        {"hourly": 2, "daily": 100, "dedup_minutes": 10, "failure_threshold": 3},
    )
    event_id = uuid.uuid4().hex
    store.put(
        "events",
        event_id,
        {
            "event_id": event_id,
            "account_slot": "alpha",
            "capability": "keyword-engagement",
            "risk_level": "L1",
            "state": "PARTIAL_SUCCESS",
            "finished_at": datetime.now(UTC).isoformat(),
            "result": {
                "items": [
                    {"feed_id": "feed-1", "actions": {"like": {"status": "success"}}}
                ]
            },
        },
    )

    with pytest.raises(ServiceError) as exc_info:
        QuotaService(store).check_l1_batch(account="alpha", requested_actions=2)

    assert exc_info.value.code == "RATE_LIMITED"
