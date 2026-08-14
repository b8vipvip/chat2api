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
from app.v19_patch import install_v19_patch
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
        CHAT2API_ADMIN_USERNAME="admin",
        CHAT2API_ADMIN_PASSWORD="strong-admin-password-for-v19",
        CHAT2API_ADMIN_SESSION_HOURS=24,
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def app_v19(tmp_path: Path):
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
    install_v17_1_patch(app)
    install_v18_patch(app)
    install_v19_patch(app)
    return app


def login(client: TestClient) -> None:
    response = client.post(
        "/api/admin/auth/login",
        json={"username": "admin", "password": "strong-admin-password-for-v19"},
    )
    assert response.status_code == 200


def business_token(client: TestClient) -> str:
    login(client)
    response = client.post("/api/admin/keys", json={"name": "v19 test"})
    assert response.status_code == 200
    return response.json()["token"]


def test_v19_nonstream_request_body_reaches_existing_api_routes(tmp_path: Path) -> None:
    with TestClient(app_v19(tmp_path)) as client:
        token = business_token(client)
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "gpt-5.6-sol",
                "messages": [{"role": "user", "content": "body replay"}],
            },
        )
        assert response.status_code == 503
        assert "extension" in response.text.lower()


def test_v19_health_and_admin_capabilities(tmp_path: Path) -> None:
    with TestClient(app_v19(tmp_path)) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["version"] == "0.19.0"
        login(client)
        overview = client.get("/api/admin/overview")
        assert overview.status_code == 200
        payload = overview.json()
        assert payload["version"] == "0.19.0"
        capabilities = payload["capabilities"]
        assert capabilities["nonstream_disconnect_cleanup"] is True
        assert capabilities["stale_stop_completion_recovery"] is True
        assert capabilities["conversation_warm_pool"] is True


def test_completion_recovery_is_conservative_and_loaded_after_request_v5() -> None:
    source = (EXTENSION / "content_completion_v6.js").read_text(encoding="utf-8")
    bootstrap = (EXTENSION / "content_bootstrap.js").read_text(encoding="utf-8")
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]

    assert "__CHAT2API_REQUEST_CONTENT_V5__" in source
    assert '"stale-stop-final-actions"' in source
    assert '"stale-stop-stable-text"' in source
    assert "stableMs >= 2500" in source
    assert "stableMs >= 9000" in source
    assert "transientStatusVisible" in source
    assert 'button.style.visibility = "hidden"' in source
    assert scripts.index("content_request_v5.js") < scripts.index("content_completion_v6.js")
    assert '"content_completion_v6.js"' in bootstrap
    assert manifest["version"] == "0.7.6"


def test_warm_pool_reuses_closed_routes_as_fresh_chat_and_refills_on_claim() -> None:
    source = (EXTENSION / "conversation_warm_pool_v2.js").read_text(encoding="utf-8")
    routing = (EXTENSION / "conversation_routing.js").read_text(encoding="utf-8")
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")

    assert "composerReady" in source
    assert 'document.querySelector(selector)' in source
    assert 'strategy: "composer-controller-ready"' in source
    assert 'conversation_strategy: "claim-prewarmed-window"' in source
    assert "if (route?.conversation_id) return null" not in source
    assert "resetForWarmClaim" in source
    assert '"prewarmed-after-closed-window"' in source
    assert "scheduleWarm(350)" in source
    assert "conversation_fresh_after_closed_window" in source
    assert "conversation_warm_replenish_on_claim" in source
    assert "chat2apiConversationWarmPoolV2" in source
    assert 'changes.socketState?.newValue === "connected"' in source
    assert "tab.status" not in source

    assert "IDLE_CLOSE_MS = 300000" in routing
    assert "resetClosedRoute" in routing
    assert "reopen-saved-conversation" not in routing
    assert '"closed-window-new-chat"' in routing

    assert entry.index('"conversation_routing.js"') < entry.index('"conversation_warm_pool_v2.js"')
    assert entry.index('"conversation_warm_pool_v2.js"') < entry.index('"conversation_dispatch.js"')


def test_v19_disconnect_guard_sends_cancel_and_releases_through_existing_finally() -> None:
    source = (ROOT / "app" / "v19_patch.py").read_text(encoding="utf-8")
    main_source = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    assert "request.is_disconnected()" in source
    assert '"type": "chat.cancel"' in source
    assert 'RuntimeError("API client disconnected")' in source
    assert '"disconnect_cleanup"] = "v19-cancel-and-release"' in source
    assert "registry.busy_clients.discard(client_id)" in main_source
    assert "await broker.release(request_id)" in main_source
    assert "from .v19_patch import install_v19_patch" in entry
    assert entry.index("install_v18_patch(app)") < entry.index("install_v19_patch(app)")
