from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.quota_reservations import (
    QuotaExceeded,
    QuotaReservationError,
    QuotaReservationStore,
)


class FakeApiKeys:
    def __init__(self):
        self.keys = {
            "key_1": SimpleNamespace(
                enabled=True, token_quota=100, tokens_used=0,
                charged_requests={},
                public=lambda: {"enabled": True},
            )
        }

    async def account_completed_usage(self, record):
        if record.get("status") != "completed":
            return
        item = self.keys.get(record.get("api_key_id"))
        if item is None:
            return
        request_id = record["request_id"]
        amount = int((record.get("usage") or {}).get("total_tokens") or 0)
        previous = item.charged_requests.get(request_id, 0)
        if amount > previous:
            item.charged_requests[request_id] = amount
            item.tokens_used += amount - previous
            if item.token_quota is not None and item.tokens_used >= item.token_quota:
                item.enabled = False


def completed(request_id="req_1", tokens=35):
    return {
        "request_id": request_id,
        "api_key_id": "key_1",
        "status": "completed",
        "usage": {"total_tokens": tokens},
    }


def test_reservations_are_idempotent_and_accounting_owner_stays_api_key_store(tmp_path: Path):
    store = QuotaReservationStore(tmp_path)
    first = store.reserve("req_1", "key_1", 20, quota_limit=100, completed_tokens=0)
    duplicate = store.reserve("req_1", "key_1", 99, quota_limit=100, completed_tokens=0)
    assert first["reserved_tokens"] == duplicate["reserved_tokens"] == 20
    assert store.active_reserved_tokens("key_1") == 20
    with pytest.raises(QuotaReservationError, match="another API key"):
        store.reserve("req_1", "key_2", 1, quota_limit=100, completed_tokens=0)

    api_keys = FakeApiKeys()
    result = asyncio.run(store.commit_completed_usage(completed(), api_keys))
    assert result == "settled"
    assert api_keys.keys["key_1"].tokens_used == 35
    assert api_keys.keys["key_1"].charged_requests == {"req_1": 35}
    assert store.get("req_1")["state"] == "settled"
    assert store.get("req_1")["reserved_tokens"] == 35
    assert store.active_reserved_tokens("key_1") == 0

    # Rewritten terminal usage can increase an existing idempotent charge only
    # by the delta, never charge the same request twice.
    asyncio.run(store.commit_completed_usage(completed(tokens=42), api_keys))
    assert api_keys.keys["key_1"].tokens_used == 42
    assert api_keys.keys["key_1"].charged_requests["req_1"] == 42
    store.close()


def test_quota_admission_counts_completed_and_all_inflight_reservations(tmp_path: Path):
    store = QuotaReservationStore(tmp_path)
    store.reserve("req_a", "key_1", 30, quota_limit=100, completed_tokens=40)
    store.reserve("req_b", "key_1", 29, quota_limit=100, completed_tokens=40)
    assert store.active_reserved_tokens("key_1") == 59
    with pytest.raises(QuotaExceeded):
        store.reserve("req_c", "key_1", 2, quota_limit=100, completed_tokens=40)
    store.release("req_a", "key_1")
    assert store.active_reserved_tokens("key_1") == 29
    store.reserve("req_c", "key_1", 31, quota_limit=100, completed_tokens=40)
    assert store.active_reserved_tokens("key_1") == 60
    store.close()


def test_concurrent_reservations_cannot_oversubscribe_a_shared_sqlite_quota(tmp_path: Path):
    left = QuotaReservationStore(tmp_path)
    right = QuotaReservationStore(tmp_path)

    def attempt(store, request_id):
        try:
            store.reserve(request_id, "key_1", 60, quota_limit=100, completed_tokens=0)
            return "reserved"
        except QuotaExceeded:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: attempt(*pair), [(left, "req_left"), (right, "req_right")]))
    assert sorted(results) == ["rejected", "reserved"]
    assert left.active_reserved_tokens("key_1") == 60
    left.close()
    right.close()


def test_failed_requests_release_and_committing_requests_are_not_released_or_expired(tmp_path: Path):
    store = QuotaReservationStore(tmp_path, reservation_ttl_seconds=60)
    store.reserve("req_fail", "key_1", 25, quota_limit=100, completed_tokens=0)
    failed = {
        "request_id": "req_fail", "api_key_id": "key_1",
        "status": "failed", "usage": {"total_tokens": 25},
    }
    assert asyncio.run(store.commit_completed_usage(failed, FakeApiKeys())) == "released"
    assert store.get("req_fail")["state"] == "released"
    assert store.active_reserved_tokens("key_1") == 0

    store.reserve("req_commit", "key_1", 20, quota_limit=100, completed_tokens=0)
    store.begin_commit("req_commit", "key_1", 33)
    with pytest.raises(QuotaReservationError, match="reconciled"):
        store.release("req_commit", "key_1")
    assert store.expire_reservations(now=store.get("req_commit")["expires_at"] + 1) == 0
    assert store.get("req_commit")["state"] == "committing"
    store.close()


def test_reconcile_recovers_crash_after_api_key_usage_commit(tmp_path: Path):
    store = QuotaReservationStore(tmp_path)
    api_keys = FakeApiKeys()
    store.reserve("req_recover", "key_1", 20, quota_limit=100, completed_tokens=0)
    store.begin_commit("req_recover", "key_1", 30)
    # Simulate ApiKeyStore persisted its idempotent request ledger, then process
    # crashed before the reservation journal could mark itself settled.
    api_keys.keys["key_1"].tokens_used = 30
    api_keys.keys["key_1"].charged_requests["req_recover"] = 30
    assert store.reconcile(api_keys) == 1
    assert store.get("req_recover")["state"] == "settled"
    assert store.active_reserved_tokens("key_1") == 0
    store.close()


def test_expiry_releases_only_unfinished_reservations_and_store_is_private(tmp_path: Path):
    store = QuotaReservationStore(tmp_path, reservation_ttl_seconds=60)
    row = store.reserve("req_expire", "key_1", 15, quota_limit=100, completed_tokens=0)
    assert store.expire_reservations(now=row["expires_at"] + 1) == 1
    assert store.get("req_expire")["state"] == "expired"
    assert store.active_reserved_tokens("key_1") == 0
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.close()

def test_released_and_expired_request_ids_cannot_be_reused_for_new_admission(tmp_path: Path):
    store = QuotaReservationStore(tmp_path, reservation_ttl_seconds=60)
    released = store.reserve(
        "req_released", "key_1", 20, quota_limit=100, completed_tokens=0
    )
    assert store.release("req_released", "key_1") is True
    assert store.get("req_released")["state"] == "released"
    with pytest.raises(QuotaReservationError, match="cannot be reused"):
        store.reserve(
            "req_released", "key_1", 1, quota_limit=100, completed_tokens=0
        )

    expired = store.reserve(
        "req_expired", "key_1", 20, quota_limit=100, completed_tokens=0
    )
    assert store.expire_reservations(now=expired["expires_at"] + 1) == 1
    assert store.get("req_expired")["state"] == "expired"
    with pytest.raises(QuotaReservationError, match="cannot be reused"):
        store.reserve(
            "req_expired", "key_1", 1, quota_limit=100, completed_tokens=0
        )

    # Same request ID remains idempotent while active and after settlement.
    active = store.reserve(
        "req_active", "key_1", 20, quota_limit=100, completed_tokens=0
    )
    duplicate = store.reserve(
        "req_active", "key_1", 90, quota_limit=100, completed_tokens=0
    )
    assert duplicate == active
    api_keys = FakeApiKeys()
    assert asyncio.run(
        store.commit_completed_usage(completed("req_active", 10), api_keys)
    ) == "settled"
    assert store.reserve(
        "req_active", "key_1", 99, quota_limit=100, completed_tokens=10
    )["state"] == "settled"
    store.close()

