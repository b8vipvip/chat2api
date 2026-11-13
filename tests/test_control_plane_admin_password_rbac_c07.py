from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.control_plane_v1_patch import install_control_plane_v1
from app.main import create_app
from app.v17_patch import install_v17_patch


ORIGIN = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}


def _login(client: TestClient, username: str, password: str):
    return client.post(
        "/api/admin/auth/login",
        json={"username": username, "password": password},
        headers=ORIGIN,
    )


def test_delegated_user_admin_cannot_reset_peer_but_can_rotate_own_password(tmp_path: Path) -> None:
    app = create_app(Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="root",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    ))
    install_control_plane_v1(app)
    install_v17_patch(app)

    with TestClient(app) as root:
        assert _login(root, "root", "strong-root-password").status_code == 200
        for username, role in (("user_operator", "user_admin"), ("support_operator", "support")):
            created = root.post(
                "/api/admin/control/admins",
                json={"username": username, "password": "strong-initial-password", "role": role},
                headers=ORIGIN,
            )
            assert created.status_code == 201, created.text

        with TestClient(app) as delegated:
            assert _login(delegated, "user_operator", "strong-initial-password").status_code == 200
            peer_reset = delegated.post(
                "/api/admin/control/admins/support_operator/password",
                json={"new_password": "attacker-controlled-password"},
                headers=ORIGIN,
            )
            assert peer_reset.status_code == 403
            with TestClient(app) as support:
                assert _login(support, "support_operator", "strong-initial-password").status_code == 200

            own_reset = delegated.post(
                "/api/admin/control/self/password",
                json={
                    "current_password": "strong-initial-password",
                    "new_password": "strong-rotated-password",
                },
                headers=ORIGIN,
            )
            assert own_reset.status_code == 200, own_reset.text
            assert own_reset.json()["revoked_sessions"] >= 1
            assert delegated.get("/api/admin/auth/session").json()["authenticated"] is False

        with TestClient(app) as rotated:
            assert _login(rotated, "user_operator", "strong-rotated-password").status_code == 200
