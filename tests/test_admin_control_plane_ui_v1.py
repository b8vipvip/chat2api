from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path

from app.admin import admin_response
from app.admin_control_plane_ui_v1_patch import install_admin_control_plane_ui_v1


def test_admin_console_exposes_c_views_and_one_control_plane_asset():
    html = admin_response("client-policy").body.decode("utf-8")
    assert 'data-view="control-admins"' in html
    assert 'data-view="client-policy"' in html
    assert 'data-view="client-logs"' in html
    assert 'id="view-client-logs"' in html
    assert 'id="webChatClientLogExportCount"' in html
    assert 'id="webChatClientLogExportRange"' in html
    assert "/assets/chat2api-admin-web-chat-client-logs-v1.js" in html
    assert 'data-view="registration-invitations"' in html
    assert 'id="view-registration-invitations"' in html
    assert 'id="cClientDomain"' in html
    assert 'placeholder="https://chat.example.com"' in html
    assert 'id="cClientApiKey"' in html
    assert 'id="cGuestEnabled"' in html
    assert 'id="userPasswordDialog"' in html
    assert 'id="cClientClearApiKey"' in html
    assert "Web Chat 统一 API Key" in html
    assert "客户端配置仅保存密钥 ID 引用" in html
    assert 'value="invite_only"' in html
    assert 'data-view="tenants"' not in html
    assert 'data-view="user-roles"' not in html
    assert 'data-view="subscriptions"' in html
    assert 'id="view-user-roles"' not in html
    assert 'id="view-subscriptions"' in html
    assert 'data-view="model-entitlements"' in html
    assert 'data-view="plugins"' in html
    assert 'data-view="quota-ledger"' in html
    assert ".nav button[hidden]{display:none!important}" in html
    assert 'data-view="api-keys"' not in html
    assert 'id="view-api-keys"' in html
    assert 'id="view-users"' not in html
    assert 'id="view-quota-ledger"' in html
    assert 'id="view-tenants"' not in html
    assert 'id="view-model-entitlements"' in html
    assert 'value="tenant_role"' in html
    assert 'value="user_role"' in html
    assert 'value="subscription_plan"' in html
    assert 'id="cSubHistory"' in html
    assert '/assets/chat2api-control-plane-admin-v1.js' in html
    assert 'id="adminSessionLoginDialog"' in html
    assert 'id="adminSessionOpenLogin"' in html
    assert 'id="adminSessionUser"' in html
    assert 'id="adminSessionPassword"' in html
    assert 'id="adminSessionLogin"' in html
    assert 'id="adminSessionLogout"' in html
    assert 'id="adminSessionRotatePassword"' in html
    assert 'id="adminPasswordDialog"' in html
    assert "api/admin/auth/session" in html
    assert "sessionStorage.removeItem('chat2api_admin_key')" in html
    assert "sessionStorage.setItem('chat2api_admin_key'" not in html


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
        assert "adminSessionLoginDialog" in response.text
        assert "adminSessionOpenLogin" in response.text
        assert "WEB_USER_NAV_ITEMS" in response.text
        assert "/api/admin/control/self/password" in response.text
        assert "new_password" in response.text
        assert "adminSessionRotatePassword" in response.text
        assert "loadQuotaReservations" in response.text
        assert "loadInvitations" in response.text
        assert 'data.client_domain' in response.text
        assert 'client_domain: $("cClientDomain").value.trim()' in response.text
        assert 'data.api_key_configured' in response.text
        assert 'body.api_key = apiKey' in response.text
        assert 'body.clear_api_key = true' in response.text
        assert 'cClientApiKeyStatus' in response.text
        assert "/api/admin/control/invitations" in response.text
        assert '"user-roles": ["super_admin", "user_admin"]' not in response.text
        assert '"subscriptions": ["super_admin", "billing_admin"]' in response.text
        assert "/api/admin/users/" in response.text
        assert "/role" in response.text
        assert "/api/admin/subscription-plans" in response.text
        assert "/subscription" in response.text
        assert "loadUserRoles" in response.text
        assert "loadSubscriptions" in response.text
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
    assert 'let nextRole = nextAuthenticated ? (session.role || "unknown") : null' in source
    assert 'currentAdminRole = nextRole' in source
    assert 'currentAdminRole !== "super_admin"' in source
    assert 'adminRoleResolved = sessionCheckSucceeded' in source
    assert "let adminRoleRefreshGeneration = 0" in source
    assert "if (generation !== adminRoleRefreshGeneration) return" in source
    assert "event.detail" in source
    assert "document.documentElement.dataset.adminCommerceSessionReady" in source
    assert "function applyAdminRole()" in source
    assert "let currentAdminUsername = null" in source
    assert "let adminUserLoadGeneration = 0" in source
    assert "let adminUserKeyLoadGeneration = 0" in source
    assert "if (generation !== adminUserKeyLoadGeneration" in source
    assert "let adminUserKeyLoadGeneration = 0" in source
    assert "function clearUserAdminData()" in source
    assert "currentAdminUsername !== ownerUsername" in source
    assert "if (generation !== adminUserLoadGeneration" in source
    assert 'const canReadUserKeys = () => ["super_admin", "support"].includes(currentAdminRole);' in source
    assert 'if (!canReadUserKeys()) throw new Error("当前管理员角色无权查看用户 API Key");' in source
    assert "canReadUserKeys() ? '<button class=" in source
    assert '["super_admin", "billing_admin"]' in source
    assert '["super_admin", "user_admin", "system_admin", "support"]' in source
    assert 'if (canReadAudit()) await loadAudit()' in source
    assert 'const canWritePaymentSecrets = currentAdminRole === "super_admin"' in source
    assert '!canWriteUsers()' in source
    payment_source = (Path(__file__).resolve().parents[1] / "app" / "admin_payments_v106.js").read_text(encoding="utf-8")
    assert 'currentAdminRole === "super_admin"' in payment_source
    assert 'node.disabled = !canEditSecrets' in payment_source
    assert 'currentAdminRole !== "super_admin"' in payment_source

def test_commerce_navigation_buttons_join_the_c_track_role_visibility_map():
    root = Path(__file__).resolve().parents[1]
    c_track = (root / "app" / "admin_control_plane_admin_v1.js").read_text(encoding="utf-8")
    commerce = (root / "app" / "admin_user_commerce_v104.js").read_text(encoding="utf-8")

    assert '"user-pricing": ["super_admin", "billing_admin"]' in c_track
    assert '"user-payments": ["super_admin", "billing_admin"]' in c_track
    assert '"admin-users": ["super_admin", "user_admin", "system_admin", "support"]' in c_track
    for button in ("pricingButton", "paymentButton", "usersButton"):
        assert f'{button}.dataset.cview = "true"' in commerce
    assert 'document.querySelectorAll("[data-cview]")' in c_track

def test_admin_v17_consumes_window_session_events_and_ignores_stale_bootstrap_reads():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "admin_v17.js").read_text(encoding="utf-8")
    assert 'window.addEventListener("chat2api-admin-session-changed"' in source
    assert 'document.addEventListener("chat2api-admin-session-changed"' not in source
    assert "let authStateGeneration = 0" in source
    assert "if (generation !== authStateGeneration) return" in source
    assert 'dataset.adminV17Authenticated = adminLoggedIn ? "true" : "false"' in source


def test_admin_bootstrap_uses_session_marker_without_legacy_key_controls():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "admin.py").read_text(encoding="utf-8")
    v17 = (root / "app" / "admin_v17.js").read_text(encoding="utf-8")
    assert "const connectButton=$('connect'),refreshButton=$('refresh'),adminKeyInput=$('adminKey');" not in html
    assert "adminKeyInput" not in html
    assert 'id="adminKey"' not in html
    assert "sessionStorage.setItem('chat2api_admin_key'" not in html
    assert "sessionStorage.removeItem('chat2api_admin_key')" in html
    assert 'const SESSION_MARKER = "__chat2api_admin_session__";' in v17
    assert 'key = () => adminLoggedIn ? SESSION_MARKER : "";' in v17
    assert 'if (!value || value === SESSION_MARKER) return {};' in v17
    assert 'credentials: "same-origin"' in v17

def test_web_user_navigation_is_grouped_and_duplicate_api_key_entry_is_removed():
    root = Path(__file__).resolve().parents[1]
    html = admin_response("overview").body.decode("utf-8")
    nav = html.split('<div class="nav">', 1)[1].split("</div>", 1)[0]
    assert 'data-view="control-admins"' in nav
    assert 'data-view="api-keys"' not in nav

    source = (root / "app" / "admin_control_plane_admin_v1.js").read_text(encoding="utf-8")
    assert 'group.dataset.navGroup = "web-user"' in source
    for view in (
        "client-policy", "registration-invitations",
        "subscriptions", "model-entitlements", "plugins", "quota-ledger",
        "user-pricing", "user-payments", "admin-users",
    ):
        assert f'"{view}"' in source
    assert '"tenants"' not in source
    assert '"user-roles"' not in source
    assert "selectedTenantId" not in source
    assert "loadTenants" not in source
    assert "/api/admin/tenants" not in source
    assert 'webGroup.hidden = visibleItems.length === 0' in source
    assert 'loginButton.hidden = authenticated' in source
    assert 'logoutButton.hidden = !authenticated' in source
    assert 'dialog.showModal()' in source


def test_admin_header_uses_one_modal_login_and_one_session_identity():
    root = Path(__file__).resolve().parents[1]
    html = (root / "app" / "admin.py").read_text(encoding="utf-8")
    assert 'id="status" class="muted">请登录管理员账号</div>' in html
    assert 'id="sessionAuthControls"' in html
    assert 'id="adminSessionOpenLogin"' in html
    assert 'id="adminSessionLogout"' in html
    assert 'id="adminSessionLoginDialog"' in html
    assert '<div class="sessionAuth"><input id="adminSessionUser"' not in html
    assert "$('adminKey').value=key()" not in html
    assert "$('connect').onclick" not in html
    assert "$('refresh').onclick" not in html

    v17 = (root / "app" / "admin_v17.js").read_text(encoding="utf-8")
    assert 'auth.remove()' in v17
    assert 'const hasSessionLogin = () => Boolean(' in v17
    assert 'document.getElementById("adminSessionOpenLogin")' in v17
    assert 'syncModernLoginGate' in v17
    assert 'id="refresh"' not in v17
    assert "adminIdentity" not in v17
    assert '<div class="auth">' not in html


def test_user_console_uses_configured_client_domain_for_api_docs():
    source = (Path(__file__).resolve().parents[1] / "app" / "user_console_v104.js").read_text(encoding="utf-8")
    assert 'clientDomain: ""' in source
    assert 'state.clientDomain = typeof policy.client_domain === "string" ? policy.client_domain.trim() : ""' in source
    assert 'const base = state.clientDomain || location.origin;' in source


def test_web_chat_client_diagnostics_admin_asset_uses_safe_dom_and_range_exports():
    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "admin_web_chat_client_logs_v1.js").read_text(encoding="utf-8")
    assert 'credentials: "same-origin"' in source
    assert "URL.createObjectURL(blob)" in source
    assert "client-logs" in source
    assert "textContent" in source
    assert "innerHTML" not in source
    assert "webChatClientLogExportCount" in source
    assert "webChatClientLogExportRange" in source
    assert "since" in source and "until" in source
    assert 'DOMContentLoaded' in source
    c_track = (root / "app" / "admin_control_plane_admin_v1.js").read_text(encoding="utf-8")
    assert '"client-logs": ["super_admin", "system_admin", "support"]' in c_track


def test_user_admin_default_is_the_consolidated_user_management_view():
    html = admin_response("user-roles").body.decode("utf-8")
    assert "user-roles" not in html
    assert "tenants" not in html
    source = Path(__file__).resolve().parents[1] / "app" / "admin.py"
    admin_source = source.read_text(encoding="utf-8")
    assert "user_admin:'admin-users'" in admin_source
