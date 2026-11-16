from __future__ import annotations

from pathlib import Path

from app.control_plane import ControlPlaneStore, ROLES, permission_for_admin_route


ROUTE_MATRIX = (
    ("/api/clients", "GET", "system:read"),
    ("/api/admin/extensions", "GET", "system:read"),
    ("/api/admin/extensions/client_1/disconnect", "POST", "system:write"),
    ("/api/admin/pairing-codes", "POST", "system:write"),
    ("/api/admin/control/admins", "GET", "admins:read"),
    ("/api/admin/control/admins", "POST", "admins:write"),
    ("/api/admin/control/admins/support_1/password", "POST", "users:write"),
    ("/api/admin/control/self/password", "POST", "admin:self_password"),
    ("/api/admin/control/roles", "GET", "admins:read"),
    ("/api/admin/control/client", "PUT", "clients:write"),
    ("/api/admin/control/invitations", "GET", "users:read"),
    ("/api/admin/control/invitations/inv_12345678/revoke", "POST", "users:write"),
    ("/api/admin/model-entitlements", "GET", "models:read"),
    ("/api/admin/model-entitlements/tenant/ten_123", "PUT", "models:write"),
    ("/api/admin/audit", "GET", "audit:read"),
    ("/api/admin/quota-reservations", "GET", "quota:read"),
    ("/api/admin/quota-reservations/reconcile", "POST", "quota:write"),
    ("/api/admin/tenants", "GET", "tenants:read"),
    ("/api/admin/tenants/ten_123/members/usr_123456", "DELETE", "tenants:write"),
    ("/api/admin/tenants/ten_123/keys/key_123456", "PUT", "tenants:write"),
    ("/api/admin/users", "GET", "users:read"),
    ("/api/admin/users/usr_123456", "PATCH", "users:write"),
    ("/api/admin/users/usr_123456/sessions/revoke", "POST", "users:write"),
    ("/api/admin/users/usr_123456/keys", "GET", "keys:read"),
    ("/api/admin/users/usr_123456/keys/key_123456", "PATCH", "keys:write"),
    ("/api/admin/keys", "GET", "keys:read"),
    ("/api/admin/keys/key_123456", "PATCH", "keys:write"),
    ("/api/admin/keys/key_123456/settings", "PATCH", "keys:write"),
    ("/api/admin/keys/key_123456/secret", "GET", "secrets:read"),
    ("/api/admin/user-pricing", "PUT", "billing:write"),
    ("/api/admin/user-payments", "GET", "billing:read"),
    ("/api/admin/user-payments", "PUT", "secrets:write"),
    ("/api/admin/user-payments/test", "POST", "billing:write"),
    ("/api/admin/payment-channels-v106", "GET", "billing:read"),
    ("/api/admin/payment-channels-v106", "PUT", "secrets:write"),
    ("/api/admin/payment-channels-v106/test", "POST", "billing:write"),
    ("/api/admin/payment-channels-v106/usdt-orders/o1/confirm", "POST", "billing:write"),
    ("/api/admin/models", "GET", "models:read"),
    ("/api/admin/text-models/gpt-test", "PATCH", "models:write"),
    ("/api/admin/worker-tools/catalog", "GET", "diagnostics:read"),
    ("/api/admin/worker-tools/policies", "GET", "diagnostics:read"),
    ("/api/admin/worker-tools/policies/policy_1", "PATCH", "system:write"),
    ("/api/admin/worker-tools/runs", "GET", "diagnostics:read"),
    ("/api/admin/overview", "GET", "system:read"),
    ("/api/admin/modes", "PUT", "system:write"),
    ("/api/admin/plugins", "GET", "plugins:read"),
    ("/api/admin/plugins", "POST", "plugins:write"),
    ("/api/admin/plugins/plugin_1/versions/v1", "GET", "plugins:read"),
)


def test_delegated_admin_route_matrix_is_least_privilege(tmp_path: Path) -> None:
    store = ControlPlaneStore(tmp_path, root_username="root")
    identities = {
        "system_admin": "system_agent",
        "user_admin": "user_agent",
        "api_key_admin": "key_agent",
        "billing_admin": "billing_agent",
        "plugin_admin": "plugin_agent",
        "support": "support_agent",
    }
    for role, username in identities.items():
        store.create_admin(username, "matrix-password-2026", role)

    for path, method, permission in ROUTE_MATRIX:
        assert permission_for_admin_route(path, method) == permission, (path, method)
        assert store.permitted("root", path, method)
        for role, username in identities.items():
            expected = permission in ROLES[role]
            assert store.permitted(username, path, method) is expected, (
                role, method, path, permission, expected
            )


def test_sensitive_and_unreviewed_routes_remain_root_only(tmp_path: Path) -> None:
    store = ControlPlaneStore(tmp_path, root_username="root")
    for role in ("system_admin", "user_admin", "api_key_admin", "billing_admin", "plugin_admin", "support"):
        store.create_admin(role + "_agent", "matrix-password-2026", role)

    root_only = (
        ("/api/admin/keys/key_123456/export", "GET"),
        ("/api/admin/requests", "GET"),
        ("/api/admin/requests/req_123456", "GET"),
        ("/api/admin/unreviewed-future-route", "GET"),
        ("/api/admin/unreviewed-future-route", "POST"),
    )
    for path, method in root_only:
        assert permission_for_admin_route(path, method) is None
        assert store.permitted("root", path, method)
        for role in ("system_admin", "user_admin", "api_key_admin", "billing_admin", "plugin_admin", "support"):
            assert not store.permitted(role + "_agent", path, method)
