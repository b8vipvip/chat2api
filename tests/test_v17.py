from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
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
from app.v17_crypto_patch import install_v17_crypto_patch
from app.v17_finalize_patch import install_v17_finalize_patch
from app.v17_patch import install_v17_patch
from app.v17_route_migration_patch import install_v17_route_migration_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.voice_patch import install_voice_patch


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="legacy-master-key",
        CHAT2API_PAIRING_CODE="legacy-pair-code",
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-admin-password",
        CHAT2API_ADMIN_SESSION_HOURS=24,
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def app_v17(tmp_path: Path):
    app = create_app(settings(tmp_path))
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
    return app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/admin/auth/login",
        json={"username": "owner", "password": "strong-admin-password"},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


def create_business_key(client: TestClient, name: str = "Bot") -> tuple[str, str]:
    response = client.post("/api/admin/keys", json={"name": name})
    assert response.status_code == 200
    body = response.json()
    return body["key"]["key_id"], body["token"]


def create_pairing(client: TestClient, name: str) -> tuple[str, str]:
    response = client.post("/api/admin/pairing-codes", json={"name": name})
    assert response.status_code == 200
    body = response.json()
    return body["pairing"]["pairing_id"], body["code"]


def register_device(client: TestClient, code: str, device_id: str, name: str) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": code},
        json={
            "name": name,
            "browser_name": "Chrome",
            "version": "0.7.4",
            "device_id": device_id,
            "metadata": {"runtime_id": "extension-runtime", "device_id": device_id},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    return body["client_id"], body["token"]


def wait_route(app, key_id: str, timeout: float = 2.0) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        selected = app.state.registry.route_for_key(key_id)
        if selected:
            return selected
        time.sleep(0.01)
    raise AssertionError("API key route was not assigned")


def complete_chat(websocket, message: dict, text: str = "ok") -> None:
    request_id = message["request_id"]
    websocket.send_json({"type": "chat.started", "request_id": request_id})
    websocket.send_json({"type": "chat.completed", "request_id": request_id, "text": text})


def test_admin_account_session_replaces_master_api_key(tmp_path: Path) -> None:
    with TestClient(app_v17(tmp_path)) as client:
        unauthenticated = client.get("/api/admin/overview")
        assert unauthenticated.status_code == 401

        wrong = client.post("/api/admin/auth/login", json={"username": "owner", "password": "wrong"})
        assert wrong.status_code == 401

        # The historical master secret is no longer a business or administrator API key.
        old_master = client.get("/v1/models", headers={"Authorization": "Bearer legacy-master-key"})
        assert old_master.status_code == 401
        old_admin = client.get("/api/admin/overview", headers={"Authorization": "Bearer legacy-master-key"})
        assert old_admin.status_code == 401

        login(client)
        session = client.get("/api/admin/auth/session").json()
        assert session["authenticated"] is True
        assert session["username"] == "owner"

        overview = client.get("/api/admin/overview")
        assert overview.status_code == 200
        body = overview.json()
        assert body["version"] == "0.17.0"
        assert body["capabilities"]["admin_account_login"] is True
        assert body["capabilities"]["administrator_master_api_key_removed"] is True
        assert all(row.get("key_id") != "master" for row in body.get("api_keys", []))

        key_id, token = create_business_key(client)
        assert key_id.startswith("key_")
        models = client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
        assert models.status_code == 200
        assert any(row["id"] == "gpt-5.6-sol" for row in models.json()["data"])

        logout = client.post("/api/admin/auth/logout")
        assert logout.status_code == 200
        assert client.get("/api/admin/overview").status_code == 401


def test_pairing_code_binds_one_device_and_admin_can_disable_connection(tmp_path: Path) -> None:
    with TestClient(app_v17(tmp_path)) as client:
        login(client)
        pairing_id, code = create_pairing(client, "Office Chrome")

        client_id, first_token = register_device(client, code, "device-office-001", "Office")
        same_client, rotated_token = register_device(client, code, "device-office-001", "Office renamed")
        assert same_client == client_id
        assert rotated_token != first_token

        conflict = client.post(
            "/api/extensions/register",
            headers={"X-Pairing-Code": code},
            json={
                "name": "Other",
                "browser_name": "Chrome",
                "version": "0.7.4",
                "device_id": "device-other-999",
                "metadata": {},
            },
        )
        assert conflict.status_code == 409
        assert "already bound" in conflict.json()["detail"]

        management = client.get("/api/admin/extensions").json()
        pairing = next(row for row in management["pairing_codes"] if row["pairing_id"] == pairing_id)
        assert pairing["bound_client_id"] == client_id
        assert pairing["bound_device_id"] == "device-office-001"
        assert pairing["connection_status"] == "offline"

        disabled = client.post(f"/api/admin/extensions/{client_id}/disconnect")
        assert disabled.status_code == 200
        assert disabled.json()["connection_enabled"] is False

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/extensions/{client_id}?token={rotated_token}") as websocket:
                websocket.receive_json()

        enabled = client.post(f"/api/admin/extensions/{client_id}/enable")
        assert enabled.status_code == 200
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={rotated_token}") as websocket:
            hello = websocket.receive_json()
            assert hello["type"] == "server.hello"


def test_same_api_key_random_first_assignment_then_sticky_reuse_and_explicit_override(tmp_path: Path) -> None:
    app = app_v17(tmp_path)
    with TestClient(app) as client:
        login(client)
        key_id, business_token = create_business_key(client, "Sticky Bot")

        _, code_a = create_pairing(client, "A")
        _, code_b = create_pairing(client, "B")
        client_a, token_a = register_device(client, code_a, "device-route-A", "Chrome A")
        client_b, token_b = register_device(client, code_b, "device-route-B", "Chrome B")
        assert client_a != client_b

        with client.websocket_connect(f"/ws/extensions/{client_a}?token={token_a}") as ws_a:
            ws_a.receive_json()
            with client.websocket_connect(f"/ws/extensions/{client_b}?token={token_b}") as ws_b:
                ws_b.receive_json()
                sockets = {client_a: ws_a, client_b: ws_b}

                def request(extra: dict | None = None):
                    payload = {
                        "model": "gpt-5.6-sol",
                        "messages": [{"role": "user", "content": "sticky routing"}],
                    }
                    payload.update(extra or {})
                    return client.post(
                        "/v1/chat/completions",
                        headers={"Authorization": f"Bearer {business_token}"},
                        json=payload,
                    )

                with ThreadPoolExecutor(max_workers=1) as pool:
                    first_future = pool.submit(request)
                    first_client = wait_route(app, key_id)
                    assert first_client in {client_a, client_b}
                    first_message = sockets[first_client].receive_json()
                    assert first_message["type"] == "chat.request"
                    complete_chat(sockets[first_client], first_message, "first")
                    first_response = first_future.result(timeout=5)
                    assert first_response.status_code == 200

                    second_future = pool.submit(request)
                    time.sleep(0.03)
                    assert app.state.registry.route_for_key(key_id) == first_client
                    second_message = sockets[first_client].receive_json()
                    complete_chat(sockets[first_client], second_message, "second")
                    assert second_future.result(timeout=5).status_code == 200

                    other_client = client_b if first_client == client_a else client_a
                    override_future = pool.submit(request, {"client_id": other_client})
                    override_message = sockets[other_client].receive_json()
                    complete_chat(sockets[other_client], override_message, "override")
                    assert override_future.result(timeout=5).status_code == 200
                    assert app.state.registry.route_for_key(key_id) == other_client

                    final_future = pool.submit(request)
                    final_message = sockets[other_client].receive_json()
                    complete_chat(sockets[other_client], final_message, "final")
                    assert final_future.result(timeout=5).status_code == 200
                    assert app.state.registry.route_for_key(key_id) == other_client

        saved = json.loads((tmp_path / "clients.json").read_text(encoding="utf-8"))
        assert saved["api_key_routes"][key_id] == app.state.registry.route_for_key(key_id)


def test_route_history_migration_uses_latest_request_record(tmp_path: Path) -> None:
    # Build one app to persist clients, then simulate pre-v17 request history with no
    # api_key_routes map. The next app startup must restore the last-used extension.
    app = app_v17(tmp_path)
    with TestClient(app) as client:
        login(client)
        key_id, _ = create_business_key(client, "History Bot")
        _, code_a = create_pairing(client, "History A")
        _, code_b = create_pairing(client, "History B")
        client_a, _ = register_device(client, code_a, "device-history-A", "A")
        client_b, _ = register_device(client, code_b, "device-history-B", "B")

    clients_path = tmp_path / "clients.json"
    saved = json.loads(clients_path.read_text(encoding="utf-8"))
    saved["api_key_routes"] = {}
    clients_path.write_text(json.dumps(saved), encoding="utf-8")
    history = tmp_path / "request_history.jsonl"
    history.write_text(
        "\n".join([
            json.dumps({"api_key_id": key_id, "client_id": client_a, "status": "completed"}),
            json.dumps({"api_key_id": key_id, "client_id": client_b, "status": "completed"}),
        ]) + "\n",
        encoding="utf-8",
    )

    restored_app = app_v17(tmp_path)
    with TestClient(restored_app):
        assert restored_app.state.registry.route_for_key(key_id) == client_b


def test_extension_and_console_static_contracts() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.7.4"
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    device = (EXTENSION / "background_device_v17.js").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "admin_v17.js").read_text(encoding="utf-8")
    docs = (ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    env = (ROOT / ".env.example").read_text(encoding="utf-8")

    assert '"background_device_v17.js"' in entry
    assert "deviceId" in device and "crypto?.randomUUID" in device
    assert 'body.device_id = deviceId' in device
    assert "管理员登录" in admin
    assert "扩展管理" in admin
    assert "/api/admin/pairing-codes" in admin
    assert "断开连接" in admin and "允许连接" in admin
    assert "CHAT2API_ADMIN_USERNAME" in env and "CHAT2API_ADMIN_PASSWORD" in env
    assert "CHAT2API_API_KEY=old-master-key" in env
    assert "管理员身份和业务 API 身份必须彻底分离" in docs
    assert "API Key → 扩展粘性路由" in docs


def test_production_entry_installs_v17_after_v16() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v17_patch import install_v17_patch" in source
    assert "from .v17_crypto_patch import install_v17_crypto_patch" in source
    assert "from .v17_route_migration_patch import install_v17_route_migration_patch" in source
    assert "from .v17_finalize_patch import install_v17_finalize_patch" in source
    assert source.index("install_v16_patch(app)") < source.index("install_v17_patch(app)")
