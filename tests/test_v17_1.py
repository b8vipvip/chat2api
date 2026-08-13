from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.live_voice_patch import install_live_voice_patch
from app.main import create_app
from app.v10_patch import install_v10_patch
from app.v11_patch import install_v11_patch
from app.v12_patch import install_v12_patch
from app.v13_patch import install_v13_patch
from app.v14_patch import install_v14_patch
from app.v15_patch import install_v15_patch
from app.v16_patch import install_v16_patch
from app.v17_1_patch import install_v17_1_patch
from app.v17_crypto_patch import install_v17_crypto_patch
from app.v17_finalize_patch import install_v17_finalize_patch
from app.v17_patch import install_v17_patch
from app.v17_route_migration_patch import install_v17_route_migration_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.voice_patch import install_voice_patch


ROOT = Path(__file__).resolve().parents[1]


def make_settings(tmp_path: Path, password: str) -> Settings:
    return Settings(
        CHAT2API_API_KEY="legacy-master-key",
        CHAT2API_PAIRING_CODE="legacy-pair-code",
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD=password,
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def app_v171(tmp_path: Path, password: str):
    app = create_app(make_settings(tmp_path, password))
    install_voice_patch(app)
    install_live_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    install_v12_patch(app)
    install_v13_patch(app)
    install_v14_patch(app)
    install_v15_patch(app)
    install_v16_patch(app)
    install_v17_patch(app)
    install_v17_crypto_patch(app)
    install_v17_route_migration_patch(app)
    install_v17_finalize_patch(app)
    install_v17_1_patch(app)
    return app


def login(client: TestClient, password: str) -> None:
    response = client.post("/api/admin/auth/login", json={"username": "owner", "password": password})
    assert response.status_code == 200


def test_default_placeholder_admin_password_is_refused(tmp_path: Path) -> None:
    with TestClient(app_v171(tmp_path, "change-me-admin")) as client:
        health = client.get("/healthz").json()
        assert health["version"] == "0.17.1"
        response = client.post(
            "/api/admin/auth/login",
            json={"username": "owner", "password": "change-me-admin"},
        )
        assert response.status_code == 503
        assert "CHAT2API_ADMIN_PASSWORD" in response.json()["detail"]


def test_repairing_disabled_device_does_not_reenable_it(tmp_path: Path) -> None:
    password = "real-strong-admin-password"
    app = app_v171(tmp_path, password)
    with TestClient(app) as client:
        login(client, password)
        pairing_response = client.post("/api/admin/pairing-codes", json={"name": "Office"})
        assert pairing_response.status_code == 200
        code = pairing_response.json()["code"]

        registration = {
            "name": "Chrome",
            "browser_name": "Chrome",
            "version": "0.7.4",
            "device_id": "device-secure-001",
            "metadata": {},
        }
        first = client.post("/api/extensions/register", headers={"X-Pairing-Code": code}, json=registration)
        assert first.status_code == 200
        client_id = first.json()["client_id"]

        disabled = client.post(f"/api/admin/extensions/{client_id}/disconnect")
        assert disabled.status_code == 200
        assert app.state.registry.clients[client_id].connection_enabled is False

        repaired = client.post("/api/extensions/register", headers={"X-Pairing-Code": code}, json=registration)
        assert repaired.status_code == 200
        assert repaired.json()["client_id"] == client_id
        repaired_token = repaired.json()["token"]
        assert app.state.registry.clients[client_id].connection_enabled is False

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/extensions/{client_id}?token={repaired_token}") as websocket:
                websocket.receive_json()

        enabled = client.post(f"/api/admin/extensions/{client_id}/enable")
        assert enabled.status_code == 200
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={repaired_token}") as websocket:
            assert websocket.receive_json()["type"] == "server.hello"


def test_v171_entry_and_console_asset_are_installed() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    script = (ROOT / "app" / "admin_v17_1.js").read_text(encoding="utf-8")
    assert "from .v17_1_patch import install_v17_1_patch" in entry
    assert entry.index("install_v17_finalize_patch(app)") < entry.index("install_v17_1_patch(app)")
    assert "Server Console · v0.17.1" in script
