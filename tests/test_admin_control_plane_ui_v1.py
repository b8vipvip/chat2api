from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from app.admin import admin_response
from app.admin_control_plane_ui_v1_patch import install_admin_control_plane_ui_v1


def test_admin_console_exposes_c_views_and_one_control_plane_asset():
    html = admin_response("tenants").body.decode("utf-8")
    assert 'data-view="control-admins"' in html
    assert 'data-view="client-policy"' in html
    assert 'data-view="registration-invitations"' in html
    assert 'id="view-registration-invitations"' in html
    assert 'value="invite_only"' in html
    assert 'data-view="tenants"' in html
    assert 'data-view="model-entitlements"' in html
    assert 'data-view="plugins"' in html
    assert 'data-view="quota-ledger"' in html
    assert 'data-view="api-keys"' in html
    assert 'id="view-api-keys"' in html
    assert 'id="view-users"' not in html
    assert 'id="view-quota-ledger"' in html
    assert 'id="view-tenants"' in html
    assert 'id="view-model-entitlements"' in html
    assert 'value="tenant_role"' in html
    assert '/assets/chat2api-control-plane-admin-v1.js' in html
    assert 'id="adminSessionPassword"' in html
    assert 'id="adminSessionRotatePassword"' in html
    assert 'id="adminPasswordDialog"' in html
    assert "api/admin/auth/session" in html
    assert "sessionStorage.removeItem('chat2api_admin_key')" in html
    assert "sessionStorage.setItem('chat2api_admin_key'" in html


def test_control_plane_ui_asset_is_served_with_safe_cache_and_content_headers():
    app = FastAPI()
    install_admin_control_plane_ui_v1(app)
    install_admin_control_plane_ui_v1(app)
    with TestClient(app) as client:
        response = client.get("/assets/chat2api-control-plane-admin-v1.js")
        assert response.status_code == 200
        assert "application/javascript" in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "credentials: \"same-origin\"" in response.text
        assert "textContent" in response.text
        assert "innerHTML" not in response.text
        assert "adminSessionPassword" in response.text
        assert "/api/admin/control/self/password" in response.text
        assert "new_password" in response.text
        assert "adminSessionRotatePassword" in response.text
        assert "loadQuotaReservations" in response.text
        assert "loadInvitations" in response.text
        assert "/api/admin/control/invitations" in response.text
        assert "cInviteCode" in response.text
        assert "/settings" in response.text
        assert "runtime_integration_enabled" in response.text
        assert "execute_tool" not in response.text


def test_admin_console_rejects_unknown_initial_view():
    html = admin_response("not-a-real-view").body.decode("utf-8")
    assert "__INITIAL_VIEW__" not in html
    assert "const INITIAL_VIEW='overview'" in html


def test_existing_commerce_console_is_the_single_user_and_billing_ui_owner():
    source = (Path(__file__).resolve().parents[1] / "app" / "admin_user_commerce_v104.js").read_text(encoding="utf-8")
    assert 'sessionAuthenticated ? (session.role || "unknown") : null' in source
    assert 'currentAdminRole !== "super_admin"' in source
    assert 'adminRoleResolved = sessionCheckSucceeded' in source
    assert '["super_admin", "billing_admin"]' in source
    assert '["super_admin", "user_admin", "system_admin", "support"]' in source
    assert 'if (canReadAudit()) await loadAudit()' in source
    assert 'const canWritePaymentSecrets = currentAdminRole === "super_admin"' in source
    assert '!canWriteUsers()' in source
    payment_source = (Path(__file__).resolve().parents[1] / "app" / "admin_payments_v106.js").read_text(encoding="utf-8")
    assert 'currentAdminRole === "super_admin"' in payment_source
    assert 'node.disabled = !canEditSecrets' in payment_source
    assert 'currentAdminRole !== "super_admin"' in payment_source
