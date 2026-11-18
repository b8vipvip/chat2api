from __future__ import annotations

from pathlib import Path

from app.control_plane import ControlPlaneStore, ROLES, permission_for_admin_route


EXPECTED_ROLE_PERMISSIONS = {
    "super_admin": frozenset({"*"}),
    "system_admin": frozenset({
        "system:read", "system:write", "clients:read", "clients:write",
        "models:read", "models:write", "users:read", "diagnostics:read",
        "tenants:read", "tenants:write", "quota:read", "quota:write",
        "admin:self_password",
    }),
    "user_admin": frozenset({"users:read", "users:write", "admin:self_password"}),
    "api_key_admin": frozenset({"keys:read", "keys:write", "quota:read", "quota:write", "admin:self_password"}),
    "billing_admin": frozenset({"billing:read", "billing:write", "quota:read", "admin:self_password"}),
    "plugin_admin": frozenset({"plugins:read", "plugins:write", "admin:self_password"}),
    "support": frozenset({
        "users:read", "keys:read", "user_keys:read", "models:read", "clients:read",
        "diagnostics:read", "admin:self_password",
    }),
}

# Representative method/path pairs for every explicit permission branch.
# Unknown/raw-content routes are deliberately included as root-only cases.
ROUTE_MATRIX = (
    ("GET", "/api/clients", "system:read"),
    ("POST", "/api/clients", "system:write"),
    ("GET", "/api/admin/extensions", "system:read"),
    ("POST", "/api/admin/extensions", "system:write"),
    ("POST", "/api/admin/extensions/ext_1/disconnect", "system:write"),
    ("GET", "/api/admin/pairing-codes", "system:read"),
    ("POST", "/api/admin/pairing-codes", "system:write"),
    ("PATCH", "/api/admin/pairing-codes/pair_1", "system:write"),
    ("GET", "/api/admin/control/admins", "admins:read"),
    ("POST", "/api/admin/control/admins", "admins:write"),
    ("PATCH", "/api/admin/control/admins/support_1", "admins:write"),
    ("POST", "/api/admin/control/admins/support_1/password", "users:write"),
    ("POST", "/api/admin/control/self/password", "admin:self_password"),
    ("GET", "/api/admin/control/client", "clients:read"),
    ("PUT", "/api/admin/control/client", "clients:write"),
    ("GET", "/api/admin/control/invitations", "users:read"),
    ("POST", "/api/admin/control/invitations", "users:write"),
    ("POST", "/api/admin/control/invitations/inv_1/revoke", "users:write"),
    ("GET", "/api/admin/control/roles", "admins:read"),
    ("GET", "/api/admin/model-entitlements", "models:read"),
    ("POST", "/api/admin/model-entitlements", "models:write"),
    ("PUT", "/api/admin/model-entitlements/key/key_1", "models:write"),
    ("DELETE", "/api/admin/model-entitlements/tenant/ten_1", "models:write"),
    ("GET", "/api/admin/audit", "audit:read"),
    ("GET", "/api/admin/quota-reservations", "quota:read"),
    ("POST", "/api/admin/quota-reservations/reconcile", "quota:write"),
    ("GET", "/api/admin/tenants", "tenants:read"),
    ("POST", "/api/admin/tenants", "tenants:write"),
    ("GET", "/api/admin/tenants/ten_1/members", "tenants:read"),
    ("POST", "/api/admin/tenants/ten_1/members", "tenants:write"),
    ("DELETE", "/api/admin/tenants/ten_1/members/usr_1", "tenants:write"),
    ("PUT", "/api/admin/tenants/ten_1/keys/key_1", "tenants:write"),
    ("GET", "/api/admin/users", "users:read"),
    ("POST", "/api/admin/users", "users:write"),
    ("PATCH", "/api/admin/users/usr_1", "users:write"),
    ("POST", "/api/admin/users/usr_1/sessions/revoke", "users:write"),
    ("GET", "/api/admin/users/usr_1/keys", "user_keys:read"),
    ("PATCH", "/api/admin/users/usr_1/keys/key_1", "user_keys:write"),
    ("DELETE", "/api/admin/users/usr_1/keys/key_1", "user_keys:write"),
    ("GET", "/api/admin/keys", "keys:read"),
    ("POST", "/api/admin/keys", "keys:write"),
    ("PATCH", "/api/admin/keys/key_1", "keys:write"),
    ("GET", "/api/admin/keys/key_1/secret", "secrets:read"),
    ("GET", "/api/admin/keys/key_1/settings", "keys:write"),
    ("GET", "/api/admin/user-pricing", "billing:read"),
    ("PUT", "/api/admin/user-pricing", "billing:write"),
    ("GET", "/api/admin/user-payments", "billing:read"),
    ("PUT", "/api/admin/user-payments", "secrets:write"),
    ("POST", "/api/admin/user-payments/test", "billing:write"),
    ("GET", "/api/admin/payment-channels-v106", "billing:read"),
    ("PUT", "/api/admin/payment-channels-v106", "secrets:write"),
    ("POST", "/api/admin/payment-channels-v106/test", "billing:write"),
    ("GET", "/api/admin/payment-channels-v106/usdt-orders", "billing:read"),
    ("POST", "/api/admin/payment-channels-v106/usdt-orders/order_1/confirm", "billing:write"),
    ("GET", "/api/admin/models", "models:read"),
    ("POST", "/api/admin/models", "models:write"),
    ("GET", "/api/admin/text-models", "models:read"),
    ("PATCH", "/api/admin/text-models/model_1", "models:write"),
    ("PUT", "/api/admin/text-model-minimum", "models:write"),
    ("GET", "/api/admin/worker-tools/catalog", "diagnostics:read"),
    ("GET", "/api/admin/worker-tools/policies", "diagnostics:read"),
    ("PUT", "/api/admin/worker-tools/policies/scope_1", "system:write"),
    ("GET", "/api/admin/worker-tools/runs", "diagnostics:read"),
    ("GET", "/api/admin/overview", "system:read"),
    ("GET", "/api/admin/modes", "system:read"),
    ("PUT", "/api/admin/modes", "system:write"),
    ("GET", "/api/admin/plugins", "plugins:read"),
    ("POST", "/api/admin/plugins", "plugins:write"),
    ("GET", "/api/admin/plugins/plugin_1/versions/v1", "plugins:read"),
    ("POST", "/api/admin/plugins/plugin_1/versions/v1", "plugins:write"),
    ("GET", "/api/admin/requests", None),
    ("GET", "/api/admin/requests/req_1", None),
    ("GET", "/api/admin/media-storage/files", None),
    ("GET", "/api/admin/unreviewed-future-feature", None),
)


def test_role_permission_catalog_is_explicit_and_has_no_accidental_grants():
    assert ROLES == EXPECTED_ROLE_PERMISSIONS


def test_every_delegated_role_against_the_full_admin_route_method_matrix(tmp_path: Path):
    store = ControlPlaneStore(tmp_path, "root")
    usernames = {}
    for role in EXPECTED_ROLE_PERMISSIONS:
        if role == "super_admin":
            continue
        username = f"matrix_{role}"
        store.create_admin(username, "matrix-route-password", role)
        usernames[role] = username

    for method, path, permission in ROUTE_MATRIX:
        assert permission_for_admin_route(path, method) == permission, (method, path)
        assert store.permitted("root", path, method) is True
        for role, username in usernames.items():
            expected = permission is not None and permission in EXPECTED_ROLE_PERMISSIONS[role]
            assert store.permitted(username, path, method) is expected, (role, method, path, permission)
