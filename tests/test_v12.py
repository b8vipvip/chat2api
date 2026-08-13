from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.voice_patch import install_voice_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.v10_patch import install_v10_patch
from app.v11_patch import install_v11_patch
from app.v12_patch import install_v12_patch


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def headers(token: str = "master-key") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def app_v12(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    install_v12_patch(app)
    return app


def pair_extension(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.7.0"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def complete_chat(websocket, request_id: str, text: str = "ok") -> None:
    websocket.send_json({"type": "chat.started", "request_id": request_id})
    websocket.send_json({"type": "chat.completed", "request_id": request_id, "text": text})


def test_v12_version_capabilities_and_console_script(tmp_path: Path) -> None:
    with TestClient(app_v12(tmp_path)) as client:
        assert client.get("/").json()["version"] == "0.12.0"
        assert client.get("/healthz").json()["version"] == "0.12.0"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.12.0"
        assert overview["capabilities"]["per_api_key_conversation_routing"] is True
        assert overview["capabilities"]["conversation_idle_window_cleanup"] is True
        assert overview["capabilities"]["conversation_load_budget"] is True
        html = client.get("/admin").text
        assert '/assets/chat2api-v12.js' in html
        script = client.get("/assets/chat2api-v12.js")
        assert script.status_code == 200
        assert "32 个完成回合" in script.text
        assert "96,000" in script.text
        assert "120 秒" in script.text


def test_chat_request_sends_non_secret_stable_master_key_identity(tmp_path: Path) -> None:
    with TestClient(app_v12(tmp_path)) as client:
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=headers(),
                    json={"model": "default", "messages": [{"role": "user", "content": "hello"}]},
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["routing"] == {"api_key_id": "master", "api_key_kind": "master"}
                assert "master-key" not in json.dumps(message)
                complete_chat(websocket, message["request_id"])
                response = future.result(timeout=5)
        assert response.status_code == 200


def test_managed_key_request_routes_by_key_id_not_secret(tmp_path: Path) -> None:
    with TestClient(app_v12(tmp_path)) as client:
        created = client.post(
            "/api/admin/keys",
            headers=headers(),
            json={"name": "Conversation A", "expires_in_days": 7},
        )
        assert created.status_code == 200
        token = created.json()["token"]
        key_id = created.json()["key"]["key_id"]
        client_id, extension_token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=headers(token),
                    json={"model": "default", "messages": [{"role": "user", "content": "managed key"}]},
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["routing"]["api_key_id"] == key_id
                assert message["routing"]["api_key_kind"] == "managed"
                assert token not in json.dumps(message)
                complete_chat(websocket, message["request_id"])
                response = future.result(timeout=5)
        assert response.status_code == 200


def test_extension_074_has_per_key_conversation_budget_and_idle_close() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.7.5"
    assert "alarms" in manifest["permissions"]

    routing = (EXTENSION / "conversation_routing.js").read_text(encoding="utf-8")
    assert 'STORAGE_KEY = "chat2apiConversationRoutesV1"' in routing
    assert 'match(/\\/c\\/([^/?#]+)/i)' in routing
    assert "MAX_TURNS = 32" in routing
    assert "MAX_TEXT_CHARS = 96000" in routing
    assert "MAX_ATTACHMENTS = 16" in routing
    assert "SLOW_LOAD_MS = 8000" in routing
    assert "HARD_SLOW_LOAD_MS = 15000" in routing
    assert "IDLE_CLOSE_MS = 120000" in routing
    assert "chrome.windows.create({ url: requestedUrl, focused: false" in routing
    assert "chrome.windows.remove(windowId)" in routing
    assert "conversation_url" in routing and "conversation_id" in routing
    assert "api_key_id" in routing
    assert "resolveTargetTabForRequest" in routing

    dispatch = (EXTENSION / "conversation_dispatch.js").read_text(encoding="utf-8")
    assert '"chat.request", "image.request", "voice.request"' in dispatch
    assert "message?.routing?.api_key_id" in dispatch
    assert "state.chain" in dispatch

    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    assert '"conversation_routing.js"' in entry
    assert '"conversation_dispatch.js"' in entry
    assert entry.index('"background_logging.js"') < entry.index('"conversation_routing.js"') < entry.index('"conversation_dispatch.js"')


def test_production_entry_installs_v12_after_v11() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v12_patch import install_v12_patch" in source
    assert source.index("install_v11_patch(app)") < source.index("install_v12_patch(app)")
