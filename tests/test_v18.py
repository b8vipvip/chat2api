from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

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
from app.v18_patch import install_v18_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.voice_patch import install_voice_patch


ROOT = Path(__file__).resolve().parents[1]


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="legacy-master-key",
        CHAT2API_PAIRING_CODE="legacy-pair-code",
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-admin-password-for-v18",
        CHAT2API_ADMIN_SESSION_HOURS=24,
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def app_v18(tmp_path: Path):
    app = create_app(make_settings(tmp_path))
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
    install_v18_patch(app)
    return app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "strong-admin-password-for-v18"},
    )
    assert response.status_code == 200


def create_pairing(client: TestClient, name: str = "Windows 10") -> tuple[str, str]:
    response = client.post("/api/admin/pairing-codes", json={"name": name})
    assert response.status_code == 200
    payload = response.json()
    return payload["pairing"]["pairing_id"], payload["code"]


def register_extension(client: TestClient, code: str, device_id: str = "device-v18-win10") -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": code},
        json={
            "name": "Chrome",
            "browser_name": "Chrome",
            "version": "0.7.4",
            "device_id": device_id,
            "metadata": {},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    return payload["client_id"], payload["token"]


def test_pairing_status_and_device_online_status_are_separate(tmp_path: Path) -> None:
    app = app_v18(tmp_path)
    with TestClient(app) as client:
        login(client)
        pairing_id, pairing_code = create_pairing(client)

        before = client.get("/api/admin/extensions")
        assert before.status_code == 200
        pair_row = next(row for row in before.json()["pairing_codes"] if row["pairing_id"] == pairing_id)
        assert pair_row["pairing_status"] == "unpaired"
        assert "connection_status" not in pair_row

        client_id, extension_token = register_extension(client, pairing_code)
        after_pair = client.get("/api/admin/extensions").json()
        pair_row = next(row for row in after_pair["pairing_codes"] if row["pairing_id"] == pairing_id)
        assert pair_row["pairing_status"] == "paired"
        assert pair_row["bound_client_id"] == client_id
        device_row = next(row for row in after_pair["clients"] if row["client_id"] == client_id)
        assert device_row["status"] == "offline"

        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            assert websocket.receive_json()["type"] == "server.hello"
            online = client.get("/api/admin/extensions").json()
            online_row = next(row for row in online["clients"] if row["client_id"] == client_id)
            assert online_row["status"] == "online"

        offline = client.get("/api/admin/extensions").json()
        offline_row = next(row for row in offline["clients"] if row["client_id"] == client_id)
        assert offline_row["status"] == "offline"


def test_pairing_codes_can_be_copied_without_plaintext_disk_storage(tmp_path: Path) -> None:
    app = app_v18(tmp_path)
    with TestClient(app) as client:
        login(client)
        pairing_id, pairing_code = create_pairing(client, "Copy Test")

        stored = (tmp_path / "pairing_codes.json").read_text(encoding="utf-8")
        assert pairing_code not in stored
        assert "code_ciphertext" in stored

        revealed = client.get(f"/api/admin/pairing-codes/{pairing_id}/secret")
        assert revealed.status_code == 200
        assert revealed.json()["code"] == pairing_code
        assert revealed.json()["rotated"] is False

        # v0.17 console-created pairings were hash-only. First copy safely rotates
        # such a record instead of pretending the original secret is recoverable.
        app.state.pairings.items[pairing_id].code_ciphertext = None
        rotated = client.get(f"/api/admin/pairing-codes/{pairing_id}/secret")
        assert rotated.status_code == 200
        assert rotated.json()["rotated"] is True
        assert rotated.json()["code"] != pairing_code
        assert app.state.pairings.items[pairing_id].prefix == rotated.json()["code"][:12]


def test_pairing_and_extension_history_can_be_deleted(tmp_path: Path) -> None:
    app = app_v18(tmp_path)
    with TestClient(app) as client:
        login(client)
        pairing_id, pairing_code = create_pairing(client, "Delete Test")
        client_id, _extension_token = register_extension(client, pairing_code, "device-delete-v18")

        app.state.registry.api_key_routes["key_demo"] = client_id
        delete_pairing = client.delete(f"/api/admin/pairing-codes/{pairing_id}")
        assert delete_pairing.status_code == 200
        assert delete_pairing.json()["deleted"] is True
        assert delete_pairing.json()["version"] == "0.18.0"
        after_pairing_delete = client.get("/api/admin/extensions").json()
        assert all(row["pairing_id"] != pairing_id for row in after_pairing_delete["pairing_codes"])
        assert any(row["client_id"] == client_id for row in after_pairing_delete["clients"])

        delete_client = client.delete(f"/api/admin/extensions/{client_id}")
        assert delete_client.status_code == 200
        delete_payload = delete_client.json()
        assert delete_payload["deleted"] is True
        assert delete_payload["client_id"] == client_id
        assert delete_payload["version"] == "0.18.0"
        assert client_id not in app.state.registry.clients
        assert "key_demo" not in app.state.registry.api_key_routes
        after_client_delete = client.get("/api/admin/extensions").json()
        assert all(row["client_id"] != client_id for row in after_client_delete["clients"])


def test_v18_console_semantics_and_version() -> None:
    source = (ROOT / "app" / "admin_v18.js").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))

    assert 'const VERSION = "0.18.0"' in source
    assert "已连接\\s*·" in source
    assert "配对状态" in source
    assert "扩展 ID" in source
    assert "已配对" in source and "未配对" in source
    assert "copyManagedPairing" in source
    assert "deletePairingV18" in source
    assert "deleteExtensionHistoryV18" in source
    assert 'row.status || (row.online ? "online" : "offline")' in source
    assert "row.connection_status" not in source
    assert "from .v18_patch import install_v18_patch" in entry
    assert entry.index("install_v17_1_patch(app)") < entry.index("install_v18_patch(app)")
    assert manifest["version"] == "0.7.5"


def test_v18_health_reports_current_server_version(tmp_path: Path) -> None:
    with TestClient(app_v18(tmp_path)) as client:
        assert client.get("/healthz").json()["version"] == "0.18.0"
        login(client)
        overview = client.get("/api/admin/overview").json()
        assert overview["version"] == "0.18.0"
        assert overview["capabilities"]["pairing_code_copy_and_delete"] is True
        assert overview["capabilities"]["extension_history_delete"] is True
        assert overview["capabilities"]["pairing_status_separated_from_online_status"] is True
