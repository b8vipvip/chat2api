from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
import re


def test_production_entry_installs_c_track_services_and_protects_admin_routes(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    # API identifiers must never be interpolated into executable HTML handlers.
    for relative_path in ("app/admin.py", "app/admin_v17.js"):
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        assert re.search(r'onclick\s*=\s*"[^"]*\$\{', source) is None, relative_path
    admin_source = (repository_root / "app/admin.py").read_text(encoding="utf-8")
    extension_source = (repository_root / "app/admin_v17.js").read_text(encoding="utf-8")
    assert 'data-key-action="copy"' in admin_source
    assert 'data-request-id="${esc(r.request_id)}"' in admin_source
    assert 'data-pairing-action=' in extension_source
    assert 'data-extension-action=' in extension_source
    script = r"""
from app.entry import app
from app.control_plane import ROLES, permission_for_admin_route
import re
from app.admin import admin_response
from fastapi.testclient import TestClient

assert app.state.control_plane_installed is True
assert app.state.control_plane_features_v1_installed is True
assert app.state.control_plane_integration_version == 1
assert app.state.model_entitlements_installed is True
assert any(plan.get("plan_id") == "free" for plan in app.state.user_subscriptions.list_plans())
for name in (
    "admin_sessions",
    "admin_audit",
    "user_accounts",
    "invitations",
    "tenant_memberships",
    "model_entitlements",
    "user_subscriptions",
    "product_plugin_registry",
    "quota_reservations",
):
    assert getattr(app.state, name, None) is not None, name

expected = {
    ("GET", "/api/admin/control/roles"),
    ("GET", "/api/admin/control/admins"),
    ("POST", "/api/admin/control/admins"),
    ("GET", "/api/admin/control/invitations"),
    ("POST", "/api/admin/control/invitations"),
    ("GET", "/api/admin/tenants"),
    ("POST", "/api/admin/tenants"),
    ("GET", "/api/admin/model-entitlements"),
    ("PUT", "/api/admin/model-entitlements/{subject_type}/{subject_id}"),
    ("DELETE", "/api/admin/model-entitlements/{subject_type}/{subject_id}"),
    ("GET", "/api/admin/subscription-plans"),
    ("PUT", "/api/admin/subscription-plans/{plan_id}"),
    ("PUT", "/api/admin/users/{user_id}/role"),
    ("GET", "/api/admin/users/{user_id}/subscription"),
    ("PUT", "/api/admin/users/{user_id}/subscription"),
    ("DELETE", "/api/admin/users/{user_id}/subscription"),
    ("GET", "/api/admin/plugins"),
    ("POST", "/api/admin/plugins"),
    ("GET", "/api/admin/quota-reservations"),
    ("GET", "/assets/chat2api-control-plane-admin-v1.js"),
    ("GET", "/api/client/plugins"),
    ("GET", "/api/client/config"),
}
for method, path in expected:
    matches = [
        route for route in app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    ]
    assert len(matches) == 1, (method, path, len(matches))

# Exercise every registered production admin route/method against every role.
# New or unclassified routes remain root-only until explicitly granted.
store = app.state.control_plane
role_users = {"super_admin": "c-track-root"}
for role in ROLES:
    if role == "super_admin":
        continue
    username = f"matrix_{role}"
    store.create_admin(username, "matrix-route-password", role)
    role_users[role] = username

registered_admin_routes = set()
for route in app.routes:
    route_path = getattr(route, "path", None)
    if not isinstance(route_path, str):
        continue
    if not (route_path.startswith("/api/admin/") or route_path == "/api/clients"):
        continue
    if route_path.startswith("/api/admin/auth/"):
        # Login/session/logout are available to every authenticated identity;
        # they are covered by the same-origin mutation matrix below.
        continue
    for method in getattr(route, "methods", None) or set():
        method = str(method).upper()
        if method in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}:
            registered_admin_routes.add((method, route_path))

assert registered_admin_routes
for method, route_path in sorted(registered_admin_routes):
    permission = permission_for_admin_route(route_path, method)
    for role, username in role_users.items():
        expected_access = role == "super_admin" or (
            permission is not None and permission in ROLES[role]
        )
        assert store.permitted(username, route_path, method) is expected_access, (
            role, method, route_path, permission,
        )

html = admin_response("tenants").body.decode("utf-8")
assert "/assets/chat2api-control-plane-admin-v1.js" in html
assert app.state.quota_reservation_runtime_enabled is True
assert app.state.quota_reservation_runtime_coverage == [
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/images/generations",
    "/v1/responses#image_generation (delegated to Images executor)",
    "/v1/audio/speech",
    "/v1/audio/conversations",
]

# Startup must release a new-protocol hold that never reached Worker dispatch,
# preserve an authorized in-flight hold, and reconcile each owner only once.
wallet_reconcile_calls = []
startup_price = {
    "model_id": "startup-paid-model", "enabled": True,
    "input_usd_per_million": 1.0, "cached_input_usd_per_million": 0.0,
    "output_usd_per_million": 1.0,
}
app.state.user_billing.credit("startup-recovery-user", 100)
for request_id in ("req_startup_unstarted", "req_startup_dispatched"):
    app.state.user_billing.reserve_for_request(
        "startup-recovery-user", request_id,
        model_id="startup-paid-model", estimated_cny_cents=1,
        input_tokens=1, output_tokens=1,
        price_snapshot=startup_price,
        price_catalog_snapshot={"startup-paid-model": startup_price},
        usd_cny_rate=7.0,
    )
assert app.state.user_billing.mark_dispatch_authorized(
    "req_startup_dispatched", "startup-recovery-user",
)
app.state.quota_reservations.reserve(
    "req_startup_unstarted", "startup-api-key", 10,
    quota_limit=None, completed_tokens=0,
)
app.state.quota_reservations.reserve(
    "req_startup_dispatched", "startup-api-key", 10,
    quota_limit=None, completed_tokens=0,
)
app.state.user_billing.active_reservation_user_ids = lambda: ["startup-recovery-user"]
app.state.wallet_charge_reconciler = lambda user_id: wallet_reconcile_calls.append(user_id)
for path in ("/v1/images/generations", "/v1/audio/speech", "/v1/audio/conversations"):
    assert len([
        route for route in app.routes
        if getattr(route, "path", None) == path
        and "POST" in (getattr(route, "methods", None) or set())
    ]) == 1

with TestClient(app) as client:
    assert wallet_reconcile_calls == ["startup-recovery-user"], wallet_reconcile_calls
    assert app.state.user_billing.reservations["req_startup_unstarted"]["state"] == "released"
    assert app.state.user_billing.reservations["req_startup_dispatched"]["state"] == "reserved"
    assert app.state.user_billing.reservations["req_startup_dispatched"]["dispatch_authorized_at"]
    assert app.state.quota_reservations.get("req_startup_unstarted")["state"] == "released"
    assert app.state.quota_reservations.get("req_startup_dispatched")["state"] == "reserved"
    assert app.state.telemetry.get("req_startup_unstarted")["status"] == "stalled"
    assert client.get("/api/client/plugins").json() == {
        "data": [], "execution_enabled": False,
    }
    for path in (
        "/api/admin/control/admins",
        "/api/admin/control/invitations",
        "/api/admin/tenants",
        "/api/admin/model-entitlements",
        "/api/admin/subscription-plans",
        "/api/admin/users/integration-test-user/subscription",
        "/api/admin/plugins",
        "/api/admin/quota-reservations",
    ):
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code, response.text)

    same_origin = {
        "Origin": "http://testserver",
        "Sec-Fetch-Site": "same-origin",
    }
    login = client.post(
        "/api/admin/auth/login",
        headers=same_origin,
        json={"username": "c-track-root", "password": "c-track-root-test-password"},
    )
    assert login.status_code == 200, login.text

    # Every registered admin/client state-changing route rejects requests
    # with neither Origin nor Fetch Metadata, and rejects cross-site Origin.
    write_routes = sorted({
        (str(method).upper(), route.path)
        for route in app.routes
        if isinstance(getattr(route, "path", None), str)
        and (route.path.startswith("/api/admin/") or route.path == "/api/clients")
        for method in (getattr(route, "methods", None) or set())
        if str(method).upper() in {"POST", "PUT", "PATCH", "DELETE"}
    })
    assert write_routes
    for method, route_template in write_routes:
        route_path = re.sub(r"\{[^{}]+\}", "probe", route_template)
        for label, headers in (
            ("missing-origin", {}),
            ("cross-site", {
                "Origin": "https://attacker.invalid",
                "Sec-Fetch-Site": "cross-site",
            }),
        ):
            response = client.request(method, route_path, headers=headers)
            assert response.status_code == 403, (
                label, method, route_template, response.status_code, response.text,
            )

    allowed = client.put(
        "/api/admin/control/client",
        headers=same_origin,
        json={"announcement": "same-origin route accepted"},
    )
    assert allowed.status_code == 200, allowed.text

    metadata_only = client.put(
        "/api/admin/control/client",
        headers={"Sec-Fetch-Site": "same-origin"},
        json={"announcement": "same-origin fetch metadata accepted"},
    )
    assert metadata_only.status_code == 200, metadata_only.text

    # The Worker-switch middleware short-circuits the normal v17 pipeline.
    # A delegated support Session must not gain system:write through that path.
    support_login = client.post(
        "/api/admin/auth/login",
        headers=same_origin,
        json={"username": role_users["support"], "password": "matrix-route-password"},
    )
    assert support_login.status_code == 200, support_login.text
    delegated_worker_action = client.post(
        "/api/admin/extensions/probe/disconnect",
        headers=same_origin,
    )
    assert delegated_worker_action.status_code == 403, (
        delegated_worker_action.status_code, delegated_worker_action.text,
    )

print("C-track production entry integration passed")
"""
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "isolated-c-track-data")
    env["CHAT2API_API_KEY"] = "c-track-production-entry-test-key"
    env["CHAT2API_ADMIN_USERNAME"] = "c-track-root"
    env["CHAT2API_ADMIN_PASSWORD"] = "c-track-root-test-password"
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "C-track production entry integration passed" in result.stdout
