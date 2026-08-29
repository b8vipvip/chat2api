from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.playground_chat_patch import ASSET_PATH, PATCH_ID, install_playground_chat_patch
from app.runtime_contract import SERVER_RUNTIME_VERSION, version_contract_payload


def _app() -> FastAPI:
    app = FastAPI()
    app.state.playground_lifecycle_patch_installed = True

    @app.get("/admin", response_class=HTMLResponse)
    async def admin() -> str:
        return "<html><body><section id='view-playground'></section></body></html>"

    install_playground_chat_patch(app)
    return app


def test_playground_chat_patch_injects_console_asset() -> None:
    app = _app()
    with TestClient(app) as client:
        response = client.get("/admin")
        assert response.status_code == 200
        assert f'<script src="{ASSET_PATH}"></script>' in response.text

        asset = client.get(ASSET_PATH)
        assert asset.status_code == 200
        source = asset.text
        assert 'panel.id = "playgroundChatPanel"' in source
        assert 'prompt_mode: "full"' in source
        assert 'stream: true' in source
        assert 'messages: normalizedHistory()' in source
        assert 'attachments: uploaded.map' in source
        assert 'sessionStorage.setItem' in source
        assert 'Shift+Enter' in source
        assert 'X-Chat2API-Request-ID' in source


def test_playground_chat_patch_is_idempotent() -> None:
    app = _app()
    assert app.state.playground_chat_patch_installed is True
    assert install_playground_chat_patch(app) is app
    assert PATCH_ID == "playground-chat-v1"


def test_runtime_contract_advertises_playground_chat() -> None:
    app = FastAPI()
    app.version = SERVER_RUNTIME_VERSION
    payload = version_contract_payload(app)
    assert payload["features"]["playground_chat_window"] is True
    assert "playground-chat-v1" in payload["server"]["feature_revision"]
