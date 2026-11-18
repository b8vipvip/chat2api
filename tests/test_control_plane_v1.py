from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.admin_audit import AdminAuditError
from app.admin_auth import AdminLoginRateLimiter, AdminSessionStore, same_origin_admin_request
from app.config import Settings
from app.control_plane import ControlPlaneError, ControlPlaneStore, ROLES, permission_for_admin_route
from app.control_plane_v1_patch import install_control_plane_v1
from app.tenant_membership_v1_patch import install_tenant_memberships
from app.model_entitlement_v1_patch import install_model_entitlements
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


def test_client_policy_audit_completion_failure_preserves_committed_success(tmp_path: Path, monkeypatch):
    app = make_app(tmp_path)
    audit = app.state.admin_audit
    original_append = audit.append

    def fail_requested(**kwargs):
        if kwargs["action"] == "client.policy_requested":
            raise AdminAuditError("simulated audit failure")
        return original_append(**kwargs)

    monkeypatch.setattr(audit, "append", fail_requested)
    with TestClient(app) as client:
        assert login(client, "owner", "strong-root-password").status_code == 200
        denied = client.put(
            "/api/admin/control/client", headers=ORIGIN, json={"announcement": "maintenance"}
        )
        assert denied.status_code == 503
        assert app.state.control_plane.public_client()["announcement"] == ""

        def fail_completion(**kwargs):
            if kwargs["action"] == "client.policy_completed":
                raise AdminAuditError("simulated completion failure")
            return original_append(**kwargs)

        monkeypatch.setattr(audit, "append", fail_completion)
        committed = client.put(
            "/api/admin/control/client",
            headers={**ORIGIN, "X-Request-ID": "control-audit-123"},
            json={"announcement": "maintenance"},
        )
        assert committed.status_code == 200, committed.text
        assert committed.headers["X-Admin-Audit-Status"] == "completion-pending"
        assert app.state.control_plane.public_client()["announcement"] == "maintenance"
        assert app.state.admin_audit_completion_pending is True
        rows = audit.recent(20)
        assert any(row["action"] == "client.policy_requested" and row["request_id"] == "control-audit-123" for row in rows)
        assert not any(row["action"] == "client.policy_completed" for row in rows)


def test_role_route_map_is_explicit_and_secret_read_denied_to_key_admin():
    assert permission_for_admin_route("/api/admin/users/u1", "PATCH") == "users:write"
    assert permission_for_admin_route("/api/admin/users/u1/keys", "GET") == "user_keys:read"
    assert permission_for_admin_route("/api/admin/users/u1/keys/key1", "PATCH") == "user_keys:write"
    assert "user_keys:read" not in ROLES["api_key_admin"]
    assert "user_keys:read" in ROLES["support"]
    assert "user_keys:write" not in ROLES["support"]
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


def test_control_plane_load_repairs_existing_file_permissions(tmp_path: Path):
    if os.name == "nt":
        pytest.skip("POSIX file mode bits are not enforced on Windows")
    store = ControlPlaneStore(tmp_path, "owner")
    store.create_admin("support_agent", "long-enough-password", "support")
    os.chmod(store.path, 0o644)

    reopened = ControlPlaneStore(tmp_path, "owner")

    assert reopened.verify_admin("support_agent", "long-enough-password")
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_control_plane_atomic_save_never_rolls_memory_back_after_disk_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    store = ControlPlaneStore(tmp_path, "owner")

    def fail_chmod(*_args, **_kwargs):
        raise OSError("simulated post-commit chmod failure")

    monkeypatch.setattr("app.control_plane.os.chmod", fail_chmod)

    updated = store.update_client({"announcement": "committed"})

    persisted = json.loads(store.path.read_text(encoding="utf-8"))
    assert updated["announcement"] == "committed"
    assert store.public_client()["announcement"] == "committed"
    assert persisted["client"]["announcement"] == "committed"


def test_user_role_and_subscription_admin_routes_have_distinct_rbac_permissions():
    from app.control_plane import permission_for_admin_route

    assert permission_for_admin_route("/api/admin/users/usr_12345/role", "PUT") == "users:write"
    assert permission_for_admin_route("/api/admin/users/usr_12345/subscription", "GET") == "billing:read"
    assert permission_for_admin_route("/api/admin/users/usr_12345/subscription", "PUT") == "billing:write"
    assert permission_for_admin_route("/api/admin/users/usr_12345/subscription", "DELETE") == "billing:write"
    assert permission_for_admin_route("/api/admin/subscription-plans", "GET") == "billing:read"
    assert permission_for_admin_route("/api/admin/subscription-plans/pro", "PUT") == "billing:write"
    assert permission_for_admin_route("/api/admin/model-entitlements/user_role/trusted", "PUT") == "models:write"
    assert permission_for_admin_route("/api/admin/model-entitlements/subscription_plan/pro", "PUT") == "models:write"



def test_same_origin_admin_request_requires_exact_browser_origin() -> None:
    def request(headers: dict[str, str], scheme: str = "https"):
        return SimpleNamespace(headers=headers, url=SimpleNamespace(scheme=scheme))

    host = "admin.example:8443"
    valid = {
        "host": host,
        "origin": f"https://{host}",
        "x-forwarded-proto": "https, http",
        "sec-fetch-site": "same-origin",
    }
    assert same_origin_admin_request(request(valid, scheme="http")) is True

    # Origin remains mandatory even when Fetch Metadata is absent.
    assert same_origin_admin_request(request({
        "host": host,
        "origin": f"https://{host}",
    })) is True

    invalid_headers = (
        {"host": host},
        {"host": host, "origin": "https://attacker.example"},
        {"host": host, "origin": f"https://{host}.attacker.example"},
        {"host": host, "origin": f"https://user@{host}"},
        {"host": host, "origin": f"https://{host}/admin"},
        {"host": host, "origin": f"https://{host}?next=/"},
        {"host": host, "origin": f"https://{host}#fragment"},
        {"host": host, "origin": f"http://{host}"},
        {"host": host, "origin": f"https://{host}", "sec-fetch-site": "cross-site"},
        {"host": host, "origin": f"https://{host}", "sec-fetch-site": "same-site"},
    )
    for headers in invalid_headers:
        assert same_origin_admin_request(request(headers)) is False

    # An invalid forwarded scheme is not trusted; the request URL scheme is
    # used instead, so an HTTPS Origin cannot pass an HTTP request.
    assert same_origin_admin_request(request({
        "host": host,
        "origin": f"https://{host}",
        "x-forwarded-proto": "javascript",
    }, scheme="http")) is False



def test_workspace_owner_role_transitions_are_super_admin_only(tmp_path: Path):
    app = make_app(tmp_path)
    install_tenant_memberships(app)

    with TestClient(app) as root:
        assert login(root, "owner", "strong-root-password").status_code == 200
        created_admin = root.post(
            "/api/admin/control/admins",
            headers=ORIGIN,
            json={
                "username": "tenant_operator",
                "password": "strong-tenant-operator-password",
                "role": "system_admin",
            },
        )
        assert created_admin.status_code == 201, created_admin.text

        tenant_response = root.post(
            "/api/admin/tenants",
            headers=ORIGIN,
            json={"slug": "owner-guard-team", "display_name": "Owner Guard Team"},
        )
        assert tenant_response.status_code == 201, tenant_response.text
        tenant_id = tenant_response.json()["tenant"]["tenant_id"]

        first_user = root.post("/api/user/register", json={
            "email": "owner-guard-first@example.com",
            "password": "owner-guard-first-password",
        })
        second_user = root.post("/api/user/register", json={
            "email": "owner-guard-second@example.com",
            "password": "owner-guard-second-password",
        })
        assert first_user.status_code == second_user.status_code == 200
        first_id = first_user.json()["user"]["user_id"]
        second_id = second_user.json()["user"]["user_id"]

        initial_owner = root.put(
            f"/api/admin/tenants/{tenant_id}/members",
            headers=ORIGIN,
            json={"user_id": first_id, "role": "owner"},
        )
        assert initial_owner.status_code == 200, initial_owner.text

        with TestClient(app) as delegated:
            assert login(
                delegated, "tenant_operator", "strong-tenant-operator-password",
            ).status_code == 200

            # A delegated administrator has tenants:write, but cannot create
            # a new owner, demote an existing owner, or remove an owner.
            promote = delegated.put(
                f"/api/admin/tenants/{tenant_id}/members",
                headers=ORIGIN,
                json={"user_id": second_id, "role": "owner"},
            )
            assert promote.status_code == 403, promote.text

            demote = delegated.put(
                f"/api/admin/tenants/{tenant_id}/members",
                headers=ORIGIN,
                json={"user_id": first_id, "role": "member"},
            )
            assert demote.status_code == 403, demote.text

            remove = delegated.delete(
                f"/api/admin/tenants/{tenant_id}/members/{first_id}",
                headers=ORIGIN,
            )
            assert remove.status_code == 403, remove.text

            # Ordinary delegated member management remains available.
            member = delegated.put(
                f"/api/admin/tenants/{tenant_id}/members",
                headers=ORIGIN,
                json={"user_id": second_id, "role": "member"},
            )
            assert member.status_code == 200, member.text

            # The invitation path is separately constrained to non-owner roles.
            owner_invite = delegated.post(
                f"/api/admin/tenants/{tenant_id}/invitations",
                headers=ORIGIN,
                json={"email": "owner-invite@example.com", "role": "owner"},
            )
            assert owner_invite.status_code == 400, owner_invite.text

            rows = delegated.get(f"/api/admin/tenants/{tenant_id}/members")
            assert rows.status_code == 200
            roles = {row["user_id"]: row["role"] for row in rows.json()["data"]}
            assert roles == {first_id: "owner", second_id: "member"}

        # The configured root super_admin retains the explicit ownership path.
        second_owner = root.put(
            f"/api/admin/tenants/{tenant_id}/members",
            headers=ORIGIN,
            json={"user_id": second_id, "role": "owner"},
        )
        assert second_owner.status_code == 200, second_owner.text
        assert app.state.tenant_memberships.role_for(tenant_id, second_id) == "owner"


def test_user_status_and_session_revocation_audit_use_delegated_user_admin_identity(tmp_path: Path):
    from app.user_commerce import USER_SESSION_COOKIE

    app = make_app(tmp_path)
    with TestClient(app) as root:
        assert login(root, "owner", "strong-root-password").status_code == 200
        first = root.post("/api/user/register", json={
            "email": "audit-disable@example.com", "password": "audit-disable-password",
        })
        second = root.post("/api/user/register", json={
            "email": "audit-revoke@example.com", "password": "audit-revoke-password",
        })
        assert first.status_code == second.status_code == 200
        first_id = first.json()["user"]["user_id"]
        second_id = second.json()["user"]["user_id"]

        created = root.post("/api/admin/control/admins", headers=ORIGIN, json={
            "username": "users_auditor",
            "password": "strong-users-auditor-password",
            "role": "user_admin",
        })
        assert created.status_code == 201, created.text

        first_session = app.state.user_accounts.create_session(first_id)
        second_session = app.state.user_accounts.create_session(second_id)
        with TestClient(app) as delegated, TestClient(app) as first_client, TestClient(app) as second_client:
            first_client.cookies.set(USER_SESSION_COOKIE, first_session)
            second_client.cookies.set(USER_SESSION_COOKIE, second_session)
            assert first_client.get("/api/user/keys").status_code == 200
            assert second_client.get("/api/user/keys").status_code == 200
            assert login(
                delegated, "users_auditor", "strong-users-auditor-password",
            ).status_code == 200

            disabled = delegated.patch(
                f"/api/admin/users/{first_id}",
                headers=ORIGIN,
                json={"enabled": False},
            )
            assert disabled.status_code == 200, disabled.text
            assert disabled.json()["revoked_sessions"] >= 1
            assert first_client.get("/api/user/keys").status_code == 401
            assert second_client.get("/api/user/keys").status_code == 200

            revoked = delegated.post(
                f"/api/admin/users/{second_id}/sessions/revoke",
                headers=ORIGIN,
            )
            assert revoked.status_code == 200, revoked.text
            assert revoked.json()["revoked_sessions"] >= 1
            assert second_client.get("/api/user/keys").status_code == 401

    rows = app.state.admin_audit.recent(100)
    expected_actions = {
        "user.status_change_requested",
        "user.status_change_completed",
        "user.sessions_revoke_requested",
        "user.sessions_revoke_completed",
    }
    actor_rows = [
        row for row in rows
        if row["action"] in expected_actions and row["target_id"] in {first_id, second_id}
    ]
    assert {row["action"] for row in actor_rows} == expected_actions
    assert all(row["actor"] == "users_auditor" for row in actor_rows)


def test_user_api_key_update_and_revoke_audit_use_delegated_super_admin_identity(tmp_path: Path):
    from app.user_commerce import USER_SESSION_COOKIE

    app = make_app(tmp_path)
    with TestClient(app) as root:
        assert login(root, "owner", "strong-root-password").status_code == 200
        registered = root.post("/api/user/register", json={
            "email": "audit-key-owner@example.com",
            "password": "audit-key-owner-password",
        })
        assert registered.status_code == 200, registered.text
        user_id = registered.json()["user"]["user_id"]

        created = root.post("/api/admin/control/admins", headers=ORIGIN, json={
            "username": "key_auditor",
            "password": "strong-key-auditor-password",
            "role": "super_admin",
        })
        assert created.status_code == 201, created.text

        user_session = app.state.user_accounts.create_session(user_id)
        with TestClient(app) as user_client, TestClient(app) as delegated:
            user_client.cookies.set(USER_SESSION_COOKIE, user_session)
            created_key = user_client.post("/api/user/keys", json={"name": "Audit attribution key"})
            assert created_key.status_code == 200, created_key.text
            key_id = created_key.json()["key"]["key_id"]

            assert login(delegated, "key_auditor", "strong-key-auditor-password").status_code == 200
            updated = delegated.patch(
                f"/api/admin/users/{user_id}/keys/{key_id}",
                headers=ORIGIN,
                json={"enabled": False},
            )
            assert updated.status_code == 200, updated.text
            revoked = delegated.delete(
                f"/api/admin/users/{user_id}/keys/{key_id}",
                headers=ORIGIN,
            )
            assert revoked.status_code == 200, revoked.text

    rows = app.state.admin_audit.recent(100)
    expected_actions = {
        "api_key.update_requested",
        "api_key.update_completed",
        "api_key.revoke_requested",
        "api_key.revoke_completed",
    }
    actor_rows = [
        row for row in rows
        if row["action"] in expected_actions and row["target_id"] == key_id
    ]
    assert {row["action"] for row in actor_rows} == expected_actions
    assert all(row["actor"] == "key_auditor" for row in actor_rows)
