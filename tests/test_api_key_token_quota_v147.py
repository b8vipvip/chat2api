from __future__ import annotations

import asyncio
import json

from app.api_keys import ApiKeyStore
from app.data_keyring import DataKeyring
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


def test_api_key_recoverable_ciphertext_uses_data_keyring_envelope(tmp_path):
    async def scenario():
        keyring = DataKeyring(tmp_path)
        store = ApiKeyStore(tmp_path, keyring.root_secret, keyring=keyring)
        public, token = await store.create("keyring-test")
        raw = json.loads((tmp_path / "api_keys.json").read_text(encoding="utf-8"))
        ciphertext = next(row["token_ciphertext"] for row in raw["keys"] if row["key_id"] == public["key_id"])
        envelope = json.loads(ciphertext)
        assert envelope["format"] == DataKeyring.FORMAT
        assert envelope["purpose"] == "api_key.recoverable"
        assert token not in ciphertext

        reloaded = ApiKeyStore(tmp_path, keyring.root_secret, keyring=keyring)
        await reloaded.load()
        assert reloaded.reveal(public["key_id"]) == token

    asyncio.run(scenario())


def test_api_key_legacy_ciphertext_is_reencrypted_without_changing_authentication(tmp_path):
    async def scenario():
        keyring = DataKeyring(tmp_path)
        legacy_secret = "legacy-admin-api-key"
        legacy_store = ApiKeyStore(tmp_path, legacy_secret)
        public, token = await legacy_store.create("legacy-key")

        migrated = ApiKeyStore(tmp_path, keyring.root_secret, keyring=keyring)
        await migrated.load()
        original_hash = migrated.keys[public["key_id"]].token_hash
        assert await migrated.authenticate(token) is not None
        assert await migrated.migrate_cipher_from(legacy_secret) == 1
        assert migrated.reveal(public["key_id"]) == token
        assert await migrated.authenticate(token) is not None

        raw = json.loads((tmp_path / "api_keys.json").read_text(encoding="utf-8"))
        row = next(row for row in raw["keys"] if row["key_id"] == public["key_id"])
        assert json.loads(row["token_ciphertext"])["format"] == DataKeyring.FORMAT
        assert row["token_hash"] == original_hash

    asyncio.run(scenario())


def test_api_key_startup_validation_reads_legacy_without_rewriting(tmp_path):
    async def scenario():
        legacy_secret = "old-admin-key-readonly"
        keyring = DataKeyring(
            tmp_path,
            legacy_secrets={"api_key.recoverable": [legacy_secret]},
        )
        legacy_store = ApiKeyStore(tmp_path, legacy_secret)
        public, token = await legacy_store.create("legacy-readonly")

        store = ApiKeyStore(tmp_path, keyring.root_secret, keyring=keyring)
        store.legacy_ciphers = [__import__("app.api_keys", fromlist=["_fernet"])._fernet(legacy_secret)]
        await store.load()
        assert await store.validate_ciphertexts() == 1
        assert store.reveal(public["key_id"]) == token

        raw = json.loads((tmp_path / "api_keys.json").read_text(encoding="utf-8"))
        ciphertext = next(row["token_ciphertext"] for row in raw["keys"] if row["key_id"] == public["key_id"])
        assert not DataKeyring.looks_like_envelope(ciphertext)

    asyncio.run(scenario())
