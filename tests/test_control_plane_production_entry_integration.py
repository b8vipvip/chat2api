from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_production_entry_installs_c_track_services_and_protects_admin_routes(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    script = r"""
from app.entry import app
from app.admin import admin_response
from fastapi.testclient import TestClient

assert app.state.control_plane_installed is True
assert app.state.control_plane_features_v1_installed is True
assert app.state.control_plane_integration_version == 1
for name in (
    "admin_sessions",
    "admin_audit",
    "user_accounts",
    "invitations",
    "tenant_memberships",
    "model_entitlements",
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

html = admin_response("tenants").body.decode("utf-8")
assert "/assets/chat2api-control-plane-admin-v1.js" in html
assert app.state.quota_reservation_runtime_enabled is False

with TestClient(app) as client:
    assert client.get("/api/client/plugins").json() == {
        "data": [], "execution_enabled": False,
    }
    for path in (
        "/api/admin/control/admins",
        "/api/admin/control/invitations",
        "/api/admin/tenants",
        "/api/admin/model-entitlements",
        "/api/admin/plugins",
        "/api/admin/quota-reservations",
    ):
        response = client.get(path)
        assert response.status_code == 401, (path, response.status_code, response.text)

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
