"""Regression coverage for v1 -> v2 account schema compatibility and fail-closed loading."""
from __future__ import annotations

import json

import pytest

from app.user_commerce import UserAccountStore


def test_legacy_v1_account_file_migrates_to_v2_and_preserves_role(tmp_path):
    store = UserAccountStore(tmp_path)
    user = store.register("schema-v2@example.com", "long-enough-password")
    path = tmp_path / "user_accounts.json"

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = 1
    payload["users"][0].pop("system_role", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = UserAccountStore(tmp_path)
    assert migrated.system_role_for(user["user_id"]) == "user"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["users"][0]["system_role"] == "user"

    migrated.set_system_role(user["user_id"], "member")
    restarted = UserAccountStore(tmp_path)
    assert restarted.system_role_for(user["user_id"]) == "member"
    assert restarted.public(restarted.users[user["user_id"]])["system_role"] == "member"


def test_reserved_admin_role_in_user_account_file_is_downgraded(tmp_path):
    store = UserAccountStore(tmp_path)
    user = store.register("role-safety@example.com", "long-enough-password")
    path = tmp_path / "user_accounts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["version"] = 2
    payload["users"][0]["system_role"] = "system_admin"
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = UserAccountStore(tmp_path)
    assert reloaded.system_role_for(user["user_id"]) == "user"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["users"][0]["system_role"] == "user"


def test_corrupt_v2_account_file_fails_closed_without_rewriting(tmp_path):
    path = tmp_path / "user_accounts.json"
    original = b'{"version":2,"users":[{"user_id":"broken"}],"sessions":{}}'
    path.write_bytes(original)

    with pytest.raises(RuntimeError, match="User account store is unreadable"):
        UserAccountStore(tmp_path)

    assert path.read_bytes() == original
