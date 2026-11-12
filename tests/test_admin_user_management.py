from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_commerce import USER_SESSION_COOKIE
from app.user_console_v104_patch import install_user_console_v104_patch
from app.v17_patch import install_v17_patch


def _production_admin_app(tmp_path: Path):
    settings = Settings(
        CHAT2API_API_KEY="admin-user-test-master-key",
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-admin-password",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    return app


def _admin_login(client: TestClient) -> None:
    response = client.post(
        "/api/admin/auth/login",
        headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
        json={"username": "owner", "password": "strong-admin-password"},
    )
    assert response.status_code == 200, response.text


def test_admin_mutations_require_same_origin_for_login_and_all_admin_routes(tmp_path: Path) -> None:
    with TestClient(_production_admin_app(tmp_path)) as client:
        credentials = {"username": "owner", "password": "strong-admin-password"}

        missing_origin = client.post("/api/admin/auth/login", json=credentials)
        assert missing_origin.status_code == 403

        cross_origin = client.post(
            "/api/admin/auth/login",
            headers={"Origin": "https://attacker.example"},
            json=credentials,
        )
        assert cross_origin.status_code == 403

        _admin_login(client)
        assert client.get("/api/admin/users").status_code == 200

        missing_origin_write = client.post(
            "/api/admin/users/usr_missing/sessions/revoke",
            json={},
        )
        assert missing_origin_write.status_code == 403

        cross_site_write = client.post(
            "/api/admin/users/usr_missing/sessions/revoke",
            headers={
                "Origin": "http://testserver",
                "Sec-Fetch-Site": "cross-site",
            },
            json={},
        )
        assert cross_site_write.status_code == 403

        same_origin_missing_user = client.post(
            "/api/admin/users/usr_missing/sessions/revoke",
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            json={},
        )
        assert same_origin_missing_user.status_code == 404


def test_admin_user_management_lists_keys_disables_user_and_audits_actions(
    tmp_path: Path,
) -> None:
    with TestClient(_production_admin_app(tmp_path)) as client:
        _admin_login(client)

        accounts = client.app.state.user_accounts
        user = accounts.register("managed-user@example.com", "user-password-long", "Managed User")
        user_id = user["user_id"]
        user_session = accounts.create_session(user_id)
        client.cookies.set(USER_SESSION_COOKIE, user_session)

        created_key = client.post("/api/user/keys", json={"name": "managed-user-key"})
        assert created_key.status_code == 200, created_key.text
        api_token = created_key.json()["token"]
        key_id = created_key.json()["key"]["key_id"]

        active_api = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer " + api_token},
        )
        assert active_api.status_code == 200

        listing = client.get("/api/admin/users?q=managed-user")
        assert listing.status_code == 200
        row = next(item for item in listing.json()["data"] if item["user_id"] == user_id)
        assert row["key_count"] == 1
        assert row["active_sessions"] == 1
        assert row["enabled"] is True
        assert "password_hash" not in row
        assert "password_salt" not in row

        keys = client.get("/api/admin/users/" + user_id + "/keys")
        assert keys.status_code == 200
        assert len(keys.json()["data"]) == 1
        assert keys.json()["data"][0]["key_id"] == key_id
        assert "token" not in keys.json()["data"][0]
        assert api_token not in keys.text

        disabled = client.patch(
            "/api/admin/users/" + user_id,
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            json={"enabled": False},
        )
        assert disabled.status_code == 200, disabled.text
        assert disabled.json()["user"]["enabled"] is False
        assert disabled.json()["revoked_sessions"] == 1

        assert client.get("/api/user/me").status_code == 401
        blocked_api = client.get(
            "/v1/models",
            headers={"Authorization": "Bearer " + api_token},
        )
        assert blocked_api.status_code == 401
        assert blocked_api.json()["error"]["code"] == "user_disabled"

        enabled = client.patch(
            "/api/admin/users/" + user_id,
            headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"},
            json={"enabled": True},
        )
        assert enabled.status_code == 200
        assert enabled.json()["user"]["enabled"] is True
        # Re-enabling an account does not resurrect the revoked browser session.
        assert client.get("/api/user/me").status_code == 401

        audit = client.get("/api/admin/audit?limit=20")
        assert audit.status_code == 200
        actions = [row["action"] for row in audit.json()["data"]]
        assert "user.status_change_requested" in actions
        assert "user.status_change_completed" in actions
        assert all(len(row["event_hash"]) == 64 for row in audit.json()["data"])


def test_admin_user_routes_require_an_authenticated_admin_session(tmp_path: Path) -> None:
    with TestClient(_production_admin_app(tmp_path)) as client:
        assert client.get("/api/admin/users").status_code == 401
        assert client.get("/api/admin/audit").status_code == 401
        assert client.patch(
            "/api/admin/users/usr_missing",
            headers={"Origin": "http://testserver"},
            json={"enabled": False},
        ).status_code == 403
