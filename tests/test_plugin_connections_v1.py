from __future__ import annotations

import base64
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.plugin_connections_v1 import PluginOAuthStore, _gmail_body_text, install_plugin_connections_v1
from app.plugin_registry_v1_patch import install_plugin_registry
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
PASSWORD = "web-chat-password-123"


def make_app(tmp_path: Path):
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    ))
    install_control_plane_v1(app)
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_plugin_registry(app)
    install_plugin_connections_v1(app)
    return app


def register(client: TestClient, email: str) -> dict:
    response = client.post(
        "/api/user/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0]},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def test_plugin_provider_secrets_are_encrypted_and_admin_config_is_session_guarded(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    with TestClient(app) as client:
        assert client.get("/api/admin/plugins/providers").status_code == 401
        assert client.get("/api/user/plugins").status_code == 401

        login = client.post(
            "/api/admin/auth/login",
            json={"username": "owner", "password": "strong-root-password"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text

        denied = client.put(
            "/api/admin/plugins/providers/github",
            json={"enabled": True, "client_id": "gh-client", "client_secret": "gh-secret"},
        )
        assert denied.status_code == 403

        saved = client.put(
            "/api/admin/plugins/providers/github",
            headers=ORIGIN,
            json={
                "enabled": True, "client_id": "gh-client",
                "client_secret": "gh-secret", "private_repos": False,
            },
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["provider"]["configured"] is True
        assert saved.json()["provider"]["client_secret_configured"] is True
        assert "gh-secret" not in saved.text
        assert "gh-secret" not in store.path.read_bytes().decode("latin1")
        assert store.path.stat().st_mode & 0o777 == 0o600

        catalog = client.get("/api/client/plugins")
        assert catalog.status_code == 200
        assert [row["id"] for row in catalog.json()["data"]] == ["github"]

        settings = client.get("/api/admin/plugins/providers")
        assert settings.status_code == 200
        assert "gh-secret" not in settings.text
        assert settings.json()["data"][0]["redirect_uri"].endswith(
            "/api/user/plugins/oauth/github/callback"
        )


def test_oauth_state_is_one_time_and_bound_to_the_authenticated_user(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )

    with TestClient(app) as client:
        user = register(client, "plugin-user@example.com")
        plugins = client.get("/api/user/plugins")
        assert plugins.status_code == 200
        github = next(row for row in plugins.json()["data"] if row["id"] == "github")
        assert github["configured"] is True
        assert github["connected"] is False

        started = client.get("/api/user/plugins/oauth/github/start", follow_redirects=False)
        assert started.status_code == 303
        location = urlsplit(started.headers["location"])
        params = parse_qs(location.query)
        assert location.netloc == "github.com"
        assert params["client_id"] == ["gh-client"]
        assert params["redirect_uri"][0].endswith("/api/user/plugins/oauth/github/callback")
        state = params["state"][0]

        verifier = store.consume_state(state, "github", user["user_id"])
        assert verifier and len(verifier) >= 40
        assert store.consume_state(state, "github", user["user_id"]) is None

        second = client.get("/api/user/plugins/oauth/github/start", follow_redirects=False)
        state_two = parse_qs(urlsplit(second.headers["location"]).query)["state"][0]
        assert store.consume_state(state_two, "github", "another-user") is None
        assert store.consume_state(state_two, "github", user["user_id"]) is None


def test_plugin_connections_are_isolated_per_user_and_tokens_never_leave_the_store(tmp_path: Path):
    app = make_app(tmp_path)
    store: PluginOAuthStore = app.state.plugin_oauth_store
    store.save_provider(
        "github", enabled=True, client_id="gh-client",
        client_secret="gh-secret", private_repos=False,
    )
    with TestClient(app) as client:
        user_a = register(client, "plugin-a@example.com")
        user_b = register(client, "plugin-b@example.com")
        store.save_connection(
            user_a["user_id"], "github", "42", "octocat",
            "user-a-access-token", "", ["read:user", "public_repo"], None,
        )
        raw_db = store.path.read_bytes().decode("latin1")
        assert "user-a-access-token" not in raw_db
        assert store.connection(user_a["user_id"], "github")["access_token"] == "user-a-access-token"

        login_a = client.post(
            "/api/user/login",
            json={"email": "plugin-a@example.com", "password": PASSWORD},
        )
        assert login_a.status_code == 200
        rows_a = client.get("/api/user/plugins").json()["data"]
        assert next(row for row in rows_a if row["id"] == "github")["connected"] is True

        login_b = client.post(
            "/api/user/login",
            json={"email": "plugin-b@example.com", "password": PASSWORD},
        )
        assert login_b.status_code == 200
        rows_b = client.get("/api/user/plugins").json()["data"]
        github_b = next(row for row in rows_b if row["id"] == "github")
        assert github_b["connected"] is False
        assert github_b["account_label"] == ""
        assert store.connection(user_b["user_id"], "github") is None


def test_gmail_body_decoder_prefers_plain_text_and_ignores_attachments():
    plain = base64.urlsafe_b64encode("Hello from Gmail\nSecond line".encode()).decode().rstrip("=")
    html_body = base64.urlsafe_b64encode(b"<html><body><b>HTML fallback</b></body></html>").decode().rstrip("=")
    attachment = base64.urlsafe_b64encode(b"not message text").decode().rstrip("=")
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": plain}},
            {"mimeType": "text/html", "body": {"data": html_body}},
            {"mimeType": "application/pdf", "body": {"data": attachment}},
        ],
    }
    assert _gmail_body_text(payload) == "Hello from Gmail\nSecond line"

    html_only = {"mimeType": "text/html", "body": {"data": html_body}}
    assert _gmail_body_text(html_only) == "HTML fallback"
