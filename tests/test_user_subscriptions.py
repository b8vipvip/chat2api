from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from app.user_commerce import UserAccountStore
from app.user_subscriptions import (
    DEFAULT_PLAN_ID,
    UserSubscriptionError,
    UserSubscriptionStore,
)


def test_existing_user_accounts_migrate_to_least_privileged_role(tmp_path: Path):
    path = tmp_path / "user_accounts.json"
    path.write_text(json.dumps({
        "version": 1,
        "users": [{
            "user_id": "usr_legacy123",
            "email": "legacy@example.test",
            "display_name": "Legacy",
            "password_salt": base64.urlsafe_b64encode(bytes(16)).decode("ascii"),
            "password_hash": base64.urlsafe_b64encode(bytes(32)).decode("ascii"),
            "enabled": True,
            "key_ids": [],
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:00:00+00:00",
        }],
        "sessions": {},
    }), encoding="utf-8")

    accounts = UserAccountStore(tmp_path)
    assert accounts.system_role_for("usr_legacy123") == "user"
    assert accounts.admin_get("usr_legacy123")["system_role"] == "user"
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert persisted["version"] == 2
    assert persisted["users"][0]["system_role"] == "user"


def test_user_system_role_is_authoritative_validated_and_persistent(tmp_path: Path):
    accounts = UserAccountStore(tmp_path)
    created = accounts.register("role@example.test", "strong-password-123", "Role User")
    user_id = created["user_id"]
    assert created["system_role"] == "user"

    changed = accounts.set_system_role(user_id, "trusted")
    assert changed["system_role"] == "trusted"
    reopened = UserAccountStore(tmp_path)
    assert reopened.system_role_for(user_id) == "trusted"

    for reserved in ("super_admin", "system_admin", "billing_admin", "support"):
        with pytest.raises(ValueError):
            reopened.set_system_role(user_id, reserved)
    with pytest.raises(ValueError):
        reopened.set_system_role(user_id, "not a role")


def test_subscription_store_seeds_free_plan_and_persists_assignments(tmp_path: Path):
    store = UserSubscriptionStore(tmp_path)
    assert store.plan_exists(DEFAULT_PLAN_ID)
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "free"

    plan = store.upsert_plan("pro", "Pro")
    assert plan["enabled"] is True
    assigned = store.assign(
        "usr_customer123", "pro",
        expires_at="2099-01-01T00:00:00Z",
        assigned_by="billing-admin",
    )
    assert assigned["status"] == "active"
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "pro"

    reopened = UserSubscriptionStore(tmp_path)
    assert reopened.current_plan_for_user("usr_customer123")["plan_id"] == "pro"
    assert reopened.history_for_user("usr_customer123")[0]["subscription_id"] == assigned["subscription_id"]


def test_subscription_expiry_and_cancellation_revert_to_free(tmp_path: Path):
    store = UserSubscriptionStore(tmp_path)
    store.upsert_plan("pro", "Pro")
    assigned = store.assign(
        "usr_customer123", "pro", assigned_by="billing-admin",
    )
    store.db.execute(
        "UPDATE user_subscriptions SET expires_at=? WHERE subscription_id=?",
        ("2020-01-01T00:00:00+00:00", assigned["subscription_id"]),
    )
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "free"

    store.assign("usr_customer123", "pro", assigned_by="billing-admin")
    assert store.cancel_active("usr_customer123") == 1
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "free"
    assert [row["status"] for row in store.history_for_user("usr_customer123")].count("cancelled") == 2


def test_subscription_store_rejects_disabled_unknown_and_naive_expiry(tmp_path: Path):
    store = UserSubscriptionStore(tmp_path)
    store.upsert_plan("paused", "Paused", enabled=False)
    with pytest.raises(UserSubscriptionError, match="disabled"):
        store.assign("usr_customer123", "paused", assigned_by="admin")
    with pytest.raises(KeyError):
        store.assign("usr_customer123", "missing", assigned_by="admin")
    store.upsert_plan("pro", "Pro")
    with pytest.raises(UserSubscriptionError, match="timezone"):
        store.assign(
            "usr_customer123", "pro",
            expires_at="2099-01-01T00:00:00",
            assigned_by="admin",
        )
    with pytest.raises(UserSubscriptionError, match="cannot be disabled"):
        store.upsert_plan("free", "Free", enabled=False)



def test_disabling_a_plan_immediately_removes_its_effective_entitlement(tmp_path: Path):
    store = UserSubscriptionStore(tmp_path)
    store.upsert_plan("pro", "Pro", enabled=True)
    assigned = store.assign(
        "usr_customer123", "pro", assigned_by="billing-admin",
    )
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "pro"

    # Revocation must take effect immediately even for an unexpired grant.
    store.upsert_plan("pro", "Pro", enabled=False)
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "free"
    assert store.history_for_user("usr_customer123")[0]["subscription_id"] == assigned["subscription_id"]

    # Re-enabling a plan does not silently renew or prolong an expired grant.
    store.db.execute(
        "UPDATE user_subscriptions SET expires_at=? WHERE subscription_id=?",
        ("2020-01-01T00:00:00+00:00", assigned["subscription_id"]),
    )
    store.upsert_plan("pro", "Pro", enabled=True)
    assert store.current_plan_for_user("usr_customer123")["plan_id"] == "free"


@pytest.mark.parametrize("expires_at", [None, "2099-01-01T00:00:00Z"])
def test_identical_subscription_assignment_is_idempotent(tmp_path: Path, expires_at: str | None):
    store = UserSubscriptionStore(tmp_path)
    store.upsert_plan("pro", "Pro", enabled=True)

    first = store.assign(
        "usr_abcdef123", "pro", expires_at=expires_at, assigned_by="billing-admin",
    )
    retried = store.assign(
        "usr_abcdef123", "pro", expires_at=expires_at, assigned_by="billing-admin",
    )

    assert retried == first
    history = store.history_for_user("usr_abcdef123")
    assert len(history) == 1
    assert history[0]["subscription_id"] == first["subscription_id"]
    store.close()
