from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.tenant_membership import TenantMembershipError, TenantMembershipStore
from app.tenant_membership_v1_patch import install_tenant_memberships
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def app_with_tenants(path: Path):
    app = create_app(Settings(
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-admin-password",
        CHAT2API_DATA_DIR=str(path),
    ))
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_tenant_memberships(app)
    return app


def test_workspace_store_membership_and_last_owner_guard(tmp_path: Path):
    store = TenantMembershipStore(tmp_path)
    a = store.create("alpha-team", "Alpha Team")
    b = store.create("beta-team", "Beta Team")
    assert len(store.list_tenants()) == 2
    with pytest.raises(TenantMembershipError, match="already exists"):
        store.create("alpha-team", "Dup")
    with pytest.raises(TenantMembershipError, match="Invalid tenant slug"):
        store.create("BAD", "Wrong")
    user1, user2 = "usr_ABCDEFGH123", "usr_ABCDEFGH999"
    store.upsert_member(a["tenant_id"], user1, "owner")
    store.upsert_member(b["tenant_id"], user2, "owner")
    assert store.role_for(a["tenant_id"], user1) == "owner"
    assert store.role_for(b["tenant_id"], user1) is None
    assert [x["tenant_id"] for x in store.list_for_user(user1)] == [a["tenant_id"]]
    assert [x["tenant_id"] for x in store.list_for_user(user2)] == [b["tenant_id"]]
    with pytest.raises(TenantMembershipError, match="last workspace owner"):
        store.remove_member(a["tenant_id"], user1)
    with pytest.raises(TenantMembershipError, match="last workspace owner"):
        store.upsert_member(a["tenant_id"], user1, "member")
    store.upsert_member(a["tenant_id"], user2, "owner")
    store.bind_key(a["tenant_id"], user1, "key_123456789")
    assert store.tenant_for_key("key_123456789") == a["tenant_id"]
    assert store.user_for_key("key_123456789") == user1
    assert store.keys_for_tenant(a["tenant_id"])[0]["user_id"] == user1
    with pytest.raises(TenantMembershipError, match="already bound"):
        store.bind_key(b["tenant_id"], user2, "key_123456789")
    with pytest.raises(TenantMembershipError, match="Unbind"):
        store.remove_member(a["tenant_id"], user1)
    store.unbind_key(a["tenant_id"], "key_123456789")
    assert store.tenant_for_key("key_123456789") is None
    store.remove_member(a["tenant_id"], user1)
    assert store.role_for(a["tenant_id"], user1) is None
    assert store.role_for(a["tenant_id"], user2) == "owner"
    assert store.path.stat().st_mode & 0o777 == 0o600
    store.close()

    restored = TenantMembershipStore(tmp_path)
    assert restored.role_for(a["tenant_id"], user2) == "owner"
    restored.close()


def test_admin_and_user_routes_enforce_session_csrf_and_scoped_member_discovery(tmp_path: Path):
    app = app_with_tenants(tmp_path)
    with TestClient(app) as root:
        assert root.get("/api/admin/tenants").status_code == 401
        assert root.get("/api/user/tenants").status_code == 401
        login = root.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-admin-password",
        }, headers=ORIGIN)
        assert login.status_code == 200
        assert root.post("/api/admin/tenants", json={"slug": "org-one", "display_name": "One"}).status_code == 403
        tenant = root.post("/api/admin/tenants", headers=ORIGIN,
                           json={"slug": "org-one", "display_name": "One"})
        assert tenant.status_code == 201, tenant.text
        tid = tenant.json()["tenant"]["tenant_id"]
        other = root.post("/api/admin/tenants", headers=ORIGIN,
                          json={"slug": "org-two", "display_name": "Two"})
        assert other.status_code == 201, other.text
        tid2 = other.json()["tenant"]["tenant_id"]

        with TestClient(app) as alice, TestClient(app) as bob:
            a = alice.post("/api/user/register", json={
                "email": "alice@example.com", "password": "alice-password-123",
            })
            b = bob.post("/api/user/register", json={
                "email": "bob@example.com", "password": "bob-password-123",
            })
            assert a.status_code == b.status_code == 200
            uid1 = a.json()["user"]["user_id"]
            uid2 = b.json()["user"]["user_id"]

            assert root.put(f"/api/admin/tenants/{tid}/members", headers=ORIGIN, json={
                "user_id": uid1, "role": "owner"
            }).status_code == 200
            assert root.put(f"/api/admin/tenants/{tid2}/members", headers=ORIGIN, json={
                "user_id": uid2, "role": "owner"
            }).status_code == 200
            created_key = alice.post("/api/user/keys", json={"name": "Alice workspace key"})
            assert created_key.status_code == 200, created_key.text
            key_id = created_key.json()["key"]["key_id"]
            bound = root.put(f"/api/admin/tenants/{tid}/keys/{key_id}", headers=ORIGIN, json={
                "user_id": uid1,
            })
            assert bound.status_code == 200, bound.text
            assert app.state.tenant_memberships.tenant_for_key(key_id) == tid
            assert root.get(f"/api/admin/tenants/{tid}/keys").json()["data"][0]["key_id"] == key_id
            cross_tenant = root.put(f"/api/admin/tenants/{tid2}/keys/{key_id}", headers=ORIGIN, json={
                "user_id": uid1,
            })
            assert cross_tenant.status_code == 409
            assert root.put(f"/api/admin/tenants/{tid}/members", headers=ORIGIN, json={
                "user_id": "usr_NOT_REAL123", "role": "owner"
            }).status_code == 404
            assert root.put(f"/api/admin/tenants/{tid}/members", headers=ORIGIN, json={
                "user_id": uid2, "role": "root"
            }).status_code == 400

            a_rows = alice.get("/api/user/tenants")
            b_rows = bob.get("/api/user/tenants")
            assert [x["tenant_id"] for x in a_rows.json()["data"]] == [tid]
            assert [x["tenant_id"] for x in b_rows.json()["data"]] == [tid2]
            assert alice.get("/api/admin/tenants").status_code == 401
            assert root.delete(f"/api/admin/tenants/{tid}/members/{uid1}", headers=ORIGIN).status_code == 409
            assert root.delete(f"/api/admin/tenants/{tid}/keys/{key_id}", headers=ORIGIN).status_code == 200
            assert app.state.tenant_memberships.tenant_for_key(key_id) is None
            app.state.api_keys.keys[key_id].enabled = False
            disabled_binding = root.put(f"/api/admin/tenants/{tid}/keys/{key_id}", headers=ORIGIN, json={
                "user_id": uid1,
            })
            assert disabled_binding.status_code == 409
            assert root.get(f"/api/admin/tenants/{tid}/members").json()["data"][0]["user_id"] == uid1

        # No change to the existing Mode 2 business API's auth contract.
        assert root.get("/v1/models").status_code == 401
        assert any(x["action"] == "tenant.member_change_completed" for x in app.state.admin_audit.recent(20))


def test_tenant_api_rejects_cross_site_state_change(tmp_path: Path):
    app = app_with_tenants(tmp_path)
    with TestClient(app) as client:
        client.post("/api/admin/auth/login", json={
            "username": "owner", "password": "strong-admin-password"
        }, headers=ORIGIN)
        cross_site = client.post("/api/admin/tenants", json={
            "slug": "ab-team", "display_name": "A"
        }, headers={"Origin": "https://malicious.example", "Sec-Fetch-Site": "cross-site"})
        assert cross_site.status_code == 403
        assert app.state.tenant_memberships.list_tenants() == []
