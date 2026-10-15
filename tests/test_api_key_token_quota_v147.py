from __future__ import annotations

import asyncio

from app.api_keys import ApiKeyStore
from app.telemetry import TelemetryStore


def test_key_quota_persistent_deduplicated_and_exhaustion(tmp_path):
    async def scenario():
        store = ApiKeyStore(tmp_path, "quota-secret")
        telemetry = TelemetryStore(tmp_path)
        telemetry.usage_accounting = store.account_completed_usage
        key, secret = await store.create("quota-test")
        key_id = key["key_id"]
        assert key["token_quota"] is None
        assert await store.authenticate(secret) is not None

        await store.update(key_id, token_quota=5)
        first = {"request_id": "req_first", "api_key_id": key_id,
                 "status": "completed", "usage": {"total_tokens": 3}}
        await telemetry.upsert(first)
        await telemetry.upsert(first)  # Upsert/retry must not double-charge.
        assert store.get_public(key_id)["tokens_used"] == 3
        await telemetry.upsert({"request_id": "req_fail", "api_key_id": key_id,
                                "status": "error", "usage": {"total_tokens": 200}})
        assert store.get_public(key_id)["tokens_used"] == 3
        await telemetry.upsert({"request_id": "req_second", "api_key_id": key_id,
                                "status": "completed", "usage": {"total_tokens": 2}})
        exhausted = store.get_public(key_id)
        assert exhausted["tokens_used"] == 5
        assert exhausted["tokens_remaining"] == 0
        assert exhausted["quota_exhausted"] is True
        assert exhausted["enabled"] is False
        assert await store.authenticate(secret) is None

        reloaded = ApiKeyStore(tmp_path, "quota-secret")
        await reloaded.load()
        assert reloaded.get_public(key_id)["tokens_used"] == 5
        await reloaded.update(key_id, token_quota=8)
        assert reloaded.get_public(key_id)["enabled"] is True
        await reloaded.update(key_id, token_quota=None)
        assert reloaded.get_public(key_id)["tokens_remaining"] is None
        assert await reloaded.authenticate(secret) is not None

    asyncio.run(scenario())


def test_zero_token_quota_disables_immediately(tmp_path):
    async def scenario():
        store = ApiKeyStore(tmp_path, "quota-secret")
        key, token = await store.create("zero")
        await store.update(key["key_id"], token_quota=0)
        assert store.get_public(key["key_id"])["enabled"] is False
        assert await store.authenticate(token) is None

    asyncio.run(scenario())
