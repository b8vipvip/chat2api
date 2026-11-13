from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.admin_auth import AdminLoginRateLimiter, AdminSessionStore
from app.config import Settings
from app.control_plane import ControlPlaneError, ControlPlaneStore, ROLES, permission_for_admin_route
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def make_app(tmp_path: Path):
    settings = Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    )
    app = create_app(settings)
    install_control_plane_v1(app)
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    return app


def login(client: TestClient, name: str, password: str):
    return client.post("/api/admin/auth/login", json={"username": name, "password": password}, headers=ORIGIN)


def test_control_plane_store_fails_closed_and_never_persists_raw_password(tmp_path: Path):
    store = ControlPlaneStore(tmp_path, "owner")
    assert store.public_client()["registration_mode"] == "open"
    assert not store.path.exists()
    with pytest.raises(ControlPlaneError):
        store.create_admin("owner", "long-enough-password", "support")
    created = store.create_admin("support_agent", "long-enough-password", "support")
    assert created == {"username": "support_agent", "role": "support", "enabled": True}
    raw = store.path.read_text(encoding="utf-8")
    assert "long-enough-password" not in raw
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.verify_admin("support_agent", "long-enough-password")
    assert not store.verify_admin("support_agent", "incorrect-password")
    assert ControlPlaneStore(tmp_path, "owner").verify_admin("support_agent", "long-enough-password")
    store.update_admin("support_agent", enabled=False)
    assert not store.verify_admin("support_agent", "long-enough-password")

    store.path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ControlPlaneError, match="corrupt or unreadable"):
        ControlPlaneStore(tmp_path, "owner")
    assert store.path.read_text(encoding="utf-8") == "{broken"


def test_role_route_map_is_explicit_and_secret_read_denied_to_key_admin():
    assert permission_for_admin_route("/api/admin/users/u1", "PATCH") == "users:write"
    assert permission_for_admin_route("/api/admin/keys/id1", "PATCH") == "keys:write"
    assert permission_for_admin_route("/api/admin/keys/id1/secret", "GET") == "secrets:read"
    assert permission_for_admin_route("/api/admin/keys/id1/settings", "PATCH") == "keys:write"
    assert permission_for_admin_route("/api/admin/control/client", "PUT") == "clients:write"
    assert permission_for_admin_route("/api/admin/control/self/password", "POST") == "admin:self_password"
    assert permission_for_admin_route("/api/admin/unreviewed-future-feature", "GET") is None
    assert permission_for_admin_route("/api/admin/keys/id1/export", "GET") is None
    assert permission_for_admin_route("/api/admin/users/u1/secret", "GET") is None
    assert permission_for_admin_route("/api/admin/requests/req1", "GET") is None
    assert permission_for_admin_route("/api/admin/plugins", "POST") == "plugins:write"
    assert permission_for_admin_route("/api/admin/tenants", "GET") == "tenants:read"
    assert permission_for_admin_route("/api/admin/tenants/ten_123/members/usr_123456", "DELETE") == "tenants:write"
    assert permission_for_admin_route("/api/admin/tenants/ten_123/keys/key_123456", "PUT") == "tenants:write"
    assert permission_for_admin_route("/api/admin/payment-channels-v106/test", "POST") == "billing:write"
    assert permission_for_admin_route("/api/admin/payment-channels-v106/usdt-orders/o1/confirm", "POST") == "billing:write"
    assert permission_for_admin_route("/api/admin/text-models/gpt-5.6-sol", "PATCH") == "models:write"
    assert permission_for_admin_route("/api/admin/model-entitlements", "GET") == "models:read"
    assert permission_for_admin_route("/api/admin/model-entitlements/key/key_1", "PUT") == "models:write"
    assert permission_for_admin_route("/api/admin/model-entitlements/tenant/ten_123", "DELETE") == "models:write"
    assert permission_for_admin_route("/api/admin/model-entitlements/tenant_role/owner", "PUT") == "models:write"
    assert permission_for_admin_route("/api/admin/quota-reservations", "GET") == "quota:read"
    assert permission_for_admin_route("/api/admin/quota-reservations/reconcile", "POST") == "quota:write"
    assert permission_for_admin_route("/api/admin/user-payments", "GET") == "billing:read"
    assert permission_for_admin_route("/api/admin/user-payments", "PUT") == "secrets:write"
    assert permission_for_admin_route("/api/admin/user-payments/test", "POST") == "billing:write"
    assert "secrets:write" not in ROLES["billing_admin"]
    assert permission_for_admin_route("/api/admin/payment-channels-v106", "GET") == "billing:read"
    assert permission_for_admin_route("/api/admin/payment-channels-v106", "PUT") == "secrets:write"
    assert permission_for_admin_route("/api/admin/payment-channels-v106/test", "POST") == "billing:write"
    # Request search/detail may include user prompts and remains root-only.
    assert permission_for_admin_route("/api/admin/requests", "GET") is None
    assert permission_for_admin_route("/api/admin/requests/req_123", "GET") is None


def test_admin_login_rate_limiter_limits_identity_and_ip():
    limiter = AdminLoginRateLimiter(per_identity_limit=2, per_ip_limit=3, window_seconds=60)
    assert limiter.retry_after("root", "203.0.113.10") == 0
    limiter.record_failure("root", "203.0.113.10")
    assert limiter.retry_after("root", "203.0.113.10") == 0
    limiter.record_failure("root", "203.0.113.10")
    assert limiter.retry_after("root", "203.0.113.10") > 0
    # A successful login clears the identity bucket, not the source-IP budget.
    limiter.record_success("root", "203.0.113.10")
    assert limiter.retry_after("root", "203.0.113.10") == 0

    ip_limiter = AdminLoginRateLimiter(per_identity_limit=5, per_ip_limit=2, window_seconds=60)
    ip_limiter.record_failure("first-admin", "203.0.113.11")
    ip_limiter.record_failure("second-admin", "203.0.113.11")
    ip_limiter.record_success("first-admin", "203.0.113.11")
    assert ip_limiter.retry_after("third-admin", "203.0.113.11") > 0

    identity_limiter = AdminLoginRateLimiter(per_identity_limit=2, per_ip_limit=10, window_seconds=60)
    identity_limiter.record_failure("root-admin", "203.0.113.20")
    identity_limiter.record_failure("root-admin", "203.0.113.21")
    assert identity_limiter.retry_after("root-admin", "203.0.113.22") > 0


def test_admin_login_endpoint_returns_429_after_repeated_failures(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        for _ in range(5):
            response = login(client, "owner", "incorrect-password")
            assert response.status_code == 401
        limited = login(client, "owner", "strong-root-password")
        assert limited.status_code == 429
        assert int(limited.headers["Retry-After"]) > 0
        assert limited.headers["Cache-Control"] == "no-store"


def test_delegated_admin_session_permissions_and_disable_revoke(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as root:
        assert root.get("/api/admin/control/admins").status_code == 401
        assert login(root, "owner", "strong-root-password").status_code == 200
        assert root.get("/api/admin/control/roles").status_code == 200
        assert root.post("/api/admin/control/admins", json={
            "username": "support_1", "password": "strong-support-password", "role": "support",
        }).status_code == 403
        created = root.post("/api/admin/control/admins", json={
            "username": "support_1", "password": "strong-support-password", "role": "support",
        }, headers=ORIGIN)
        assert created.status_code == 201, created.text
        assert "password" not in json.dumps(created.json())
        assert root.get("/api/admin/control/admins").json()["data"][1]["username"] == "support_1"

        with TestClient(app) as delegated:
            assert login(delegated, "support_1", "strong-support-password").status_code == 200
            session = delegated.get("/api/admin/auth/session").json()
            assert session["authenticated"] is True
            assert session["username"] == "support_1"
            assert session["role"] == "support"
            assert delegated.get("/api/admin/users").status_code == 200
            assert delegated.get("/api/admin/keys").status_code == 200
            assert delegated.get("/api/admin/keys/missing/secret").status_code == 403
            assert delegated.get("/api/admin/control/admins").status_code == 403
            assert delegated.get("/api/admin/unreviewed-future-feature").status_code == 403
            assert delegated.patch("/api/admin/users/missing", json={"enabled": False}, headers=ORIGIN).status_code == 403
            assert delegated.put("/api/admin/control/client", json={"registration_mode": "closed"}, headers=ORIGIN).status_code == 403
            revoked = root.patch("/api/admin/control/admins/support_1", json={"enabled": False}, headers=ORIGIN)
            assert revoked.status_code == 200, revoked.text
            assert revoked.json()["revoked_sessions"] >= 1
            assert delegated.get("/api/admin/users").status_code == 401
            assert delegated.get("/api/admin/auth/session").json()["authenticated"] is False

    assert app.state.admin_audit.recent(10)
    assert ControlPlaneStore(tmp_path, "owner").role_for("support_1") is None


def test_user_admin_can_mutate_users_but_not_keys_or_billing(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as root:
        login(root, "owner", "strong-root-password")
        created_user = root.post("/api/user/register", json={
            "email": "user-admin-test@example.com", "password": "user-admin-test-password"
        })
        assert created_user.status_code == 200
        assert root.post("/api/admin/control/admins", json={
            "username": "users_agent", "password": "strong-users-password", "role": "user_admin"
        }, headers=ORIGIN).status_code == 201
        with TestClient(app) as delegated:
            assert login(delegated, "users_agent", "strong-users-password").status_code == 200
            user_list = delegated.get("/api/admin/users")
            assert user_list.status_code == 200
            assert user_list.json()["data"]
            assert "balance_cents" not in user_list.json()["data"][0]
            assert delegated.post("/api/admin/users/missing/sessions/revoke", headers=ORIGIN).status_code == 404
            assert delegated.get("/api/admin/user-pricing").status_code == 403
            assert delegated.patch("/api/admin/keys/missing", json={"enabled": False}, headers=ORIGIN).status_code == 403
            assert delegated.get("/api/admin/control/admins").status_code == 403


def test_client_flags_persist_enforce_registration_and_do_not_change_mode2(tmp_path: Path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        public = client.get("/api/client/config")
        assert public.status_code == 200
        assert public.json()["registration_mode"] == "open"
        assert client.get("/chat").status_code == 200
        assert login(client, "owner", "strong-root-password").status_code == 200

        assert client.put("/api/admin/control/client", json={"registration_mode": "closed"}).status_code == 403
        updated = client.put("/api/admin/control/client", json={
            "registration_mode": "closed", "web_chat_enabled": False,
            "announcement": "Planned maintenance"
        }, headers=ORIGIN)
        assert updated.status_code == 200, updated.text
        assert updated.json()["web_chat_enabled"] is False
        assert client.get("/chat").status_code == 403
        assert client.post("/api/user/register", json={
            "email": "new@example.com", "password": "long-password-123"
        }).status_code == 403
        assert client.get("/api/client/config").json()["announcement"] == "Planned maintenance"
        # Public business APIs must retain the Mode 2 ingress/auth contract.
        assert client.get("/v1/models").status_code == 401

        assert client.put("/api/admin/control/client", json={"arbitrary": True}, headers=ORIGIN).status_code == 422
        assert client.put("/api/admin/control/client", json={"registration_mode": "invite"}, headers=ORIGIN).status_code == 400

    restarted = make_app(tmp_path)
    with TestClient(restarted) as new_client:
        assert new_client.get("/api/client/config").json()["registration_mode"] == "closed"
        assert new_client.get("/chat").status_code == 403


def test_legacy_admin_session_storage_stays_backward_compatible(tmp_path: Path):
    path = tmp_path / "admin_sessions.json"
    root = AdminSessionStore("owner", "password", storage_path=path)
    token = root.create()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["sessions"][0]["username"] == "owner"
    del payload["sessions"][0]["username"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    revived = AdminSessionStore("owner", "password", storage_path=path)
    assert revived.identity(token) == "owner"
    assert revived.authenticate(token)
