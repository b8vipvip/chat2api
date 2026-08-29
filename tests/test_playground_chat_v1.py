from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.broker import RequestBroker
from app.playground_chat_patch import ASSET_PATH, PATCH_ID, install_playground_chat_patch
from app.runtime_contract import SERVER_RUNTIME_VERSION, version_contract_payload
from app.test_runs import TestRunStore


def _app() -> FastAPI:
    app = FastAPI()
    app.state.playground_lifecycle_patch_installed = True
    app.state.broker = RequestBroker()
    app.state.test_runs = TestRunStore(Path(tempfile.mkdtemp(prefix="chat2api-playground-chat-")))

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
        assert '/api/admin/playground/chat-records' in source
        assert '管理员 CHAT2API_API_KEY（默认）' not in source


def test_playground_chat_keeps_manual_prompt_verbatim_and_failed_turns_out_of_context() -> None:
    app = _app()
    with TestClient(app) as client:
        source = client.get(ASSET_PATH).text
    assert 'content: text' in source
    assert 'attachment_names: userFiles.map' in source
    assert '.filter(item => item.include_in_context !== false)' in source
    assert 'messages[userIndex].include_in_context = false' in source
    assert 'include_in_context: false' in source
    assert 'const userDisplay =' not in source


def test_playground_chat_turn_is_persisted_in_test_history() -> None:
    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/api/admin/playground/chat-records",
            json={
                "request_id": "req_chatrecord12345678",
                "model": "gpt-5.5-mini",
                "api_key_id": "key_test",
                "api_key_name": "te2",
                "status": "passed",
                "duration_ms": 1234.5,
                "first_token_ms": 450.0,
                "prompt": "测试聊天记录",
                "response_chars": 2,
                "attachments_count": 0,
            },
        )
        assert response.status_code == 200
        row = response.json()["run"]
        assert row["test_type"] == "chat"
        assert row["status"] == "passed"
        assert row["request_id"] == "req_chatrecord12345678"
        assert row["api_key_name"] == "te2"
        assert row["results"][0]["kind"] == "chat"
        assert app.state.test_runs.recent(1)[0]["run_id"] == row["run_id"]


def test_playground_chat_patch_is_idempotent() -> None:
    app = _app()
    assert app.state.playground_chat_patch_installed is True
    assert install_playground_chat_patch(app) is app
    assert PATCH_ID == "playground-chat-v2"


def test_runtime_contract_advertises_playground_chat() -> None:
    app = FastAPI()
    app.version = SERVER_RUNTIME_VERSION
    payload = version_contract_payload(app)
    assert payload["features"]["playground_chat_window"] is True
    assert payload["features"]["playground_chat_records"] is True
    assert payload["features"]["assistant_response_semantic_guard"] is True
    assert payload["features"]["assistant_response_semantic_recovery"] is True
    assert "playground-chat-v2" in payload["server"]["feature_revision"]
    assert "response-semantic-recovery-v51" in payload["server"]["feature_revision"]
