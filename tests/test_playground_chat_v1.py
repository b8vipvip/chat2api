from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.broker import RequestBroker
from app.playground_chat_patch import ASSET_PATH, PATCH_ID, RECORDS_ASSET_PATH, install_playground_chat_patch
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


def test_playground_chat_patch_injects_console_assets() -> None:
    app = _app()
    with TestClient(app) as client:
        response = client.get("/admin")
        assert response.status_code == 200
        assert f'<script src="{ASSET_PATH}"></script>' in response.text
        assert f'<script src="{RECORDS_ASSET_PATH}"></script>' in response.text
        assert response.text.index(ASSET_PATH) < response.text.index(RECORDS_ASSET_PATH)

        asset = client.get(ASSET_PATH)
        assert asset.status_code == 200
        source = asset.text
        assert 'panel.id = "playgroundChatPanel"' in source
        assert 'prompt_mode:"full"' in source.replace(" ", "")
        # Manual Playground now waits for the exact final response so Markdown
        # structure is not flattened by the incremental preview path.
        assert 'stream:false' in source.replace(" ", "")
        assert 'payload.choices?.[0]?.message?.content' in source
        assert 'messages:normalizedHistory()' in source.replace(" ", "")
        assert 'attachments:uploaded.map' in source.replace(" ", "")
        assert 'sessionStorage.setItem' in source
        assert 'Shift+Enter' in source
        assert 'X-Chat2API-Request-ID' in source
        assert '/api/admin/playground/chat-records' in source
        assert 'function renderMarkdown(markdown)' in source
        assert '管理员 CHAT2API_API_KEY（默认）' not in source

        records_asset = client.get(RECORDS_ASSET_PATH)
        assert records_asset.status_code == 200
        records_source = records_asset.text
        assert '/api/admin/playground/chat-runs' in records_source
        assert 'await startChatRun(requestId, body);' in records_source
        assert 'return baseFetch(input, init);' in records_source
        assert 'start-before-api-dispatch' not in records_source
        assert 'if (typeof globalThis.loadTests === "function")' in records_source
        assert 'an operator should never have a real Playground chat' in records_source


def test_playground_chat_keeps_manual_prompt_verbatim_and_failed_turns_out_of_context() -> None:
    app = _app()
    with TestClient(app) as client:
        source = client.get(ASSET_PATH).text
    compact = source.replace(" ", "")
    assert 'content:text' in compact
    assert 'attachment_names:userFiles.map' in compact
    assert '.filter(item=>item.include_in_context!==false)' in compact
    assert 'messages[userIndex].include_in_context=false' in compact
    assert 'include_in_context:false' in compact
    assert 'const userDisplay =' not in source


def test_playground_chat_running_row_exists_before_terminal_result_and_is_updated_in_place() -> None:
    app = _app()
    with TestClient(app) as client:
        started = client.post(
            "/api/admin/playground/chat-runs",
            json={
                "request_id": "req_chatrecord12345678",
                "model": "gpt-5.6-sol",
                "api_key_id": "key_test",
                "api_key_name": "bot2",
                "prompt": "你好呀，你是哪个模型",
                "attachments_count": 0,
            },
        )
        assert started.status_code == 200
        running = started.json()["run"]
        assert running["test_type"] == "chat"
        assert running["status"] == "running"
        assert running["request_id"] == "req_chatrecord12345678"
        assert running["api_key_name"] == "bot2"
        assert running["finished_at"] is None
        assert running["quality"]["record_lifecycle"] == "start-before-api-dispatch"
        assert app.state.test_runs.recent(1)[0]["run_id"] == running["run_id"]

        terminal = client.post(
            "/api/admin/playground/chat-records",
            json={
                "request_id": "req_chatrecord12345678",
                "model": "gpt-5.6-sol",
                "api_key_id": "key_test",
                "api_key_name": "bot2",
                "status": "failed",
                "duration_ms": 120.5,
                "first_token_ms": None,
                "prompt": "你好呀，你是哪个模型",
                "response_chars": 0,
                "attachments_count": 0,
                "error": "No online Worker is compatible with gpt-5.6-sol",
            },
        )
        assert terminal.status_code == 200
        finished = terminal.json()["run"]
        assert finished["run_id"] == running["run_id"]
        assert finished["status"] == "failed"
        assert finished["finished_at"]
        assert finished["results"][0]["kind"] == "chat"
        assert finished["results"][0]["request_id"] == "req_chatrecord12345678"
        matching = [row for row in app.state.test_runs.recent(50) if row.get("request_id") == "req_chatrecord12345678"]
        assert len(matching) == 1


def test_terminal_record_endpoint_remains_backward_compatible_without_start_call() -> None:
    app = _app()
    with TestClient(app) as client:
        response = client.post(
            "/api/admin/playground/chat-records",
            json={
                "request_id": "req_legacychat12345678",
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
        assert row["request_id"] == "req_legacychat12345678"
        assert row["api_key_name"] == "te2"
        assert row["results"][0]["kind"] == "chat"
        assert app.state.test_runs.recent(1)[0]["run_id"] == row["run_id"]


def test_playground_chat_patch_is_idempotent() -> None:
    app = _app()
    assert app.state.playground_chat_patch_installed is True
    assert install_playground_chat_patch(app) is app
    # v3 is the persistence-record protocol; the served UI asset is v69.
    assert PATCH_ID == "playground-chat-v3"
    with TestClient(app) as client:
        assert '__CHAT2API_PLAYGROUND_CHAT_V69__' in client.get(ASSET_PATH).text


def test_runtime_contract_advertises_playground_chat() -> None:
    app = FastAPI()
    app.version = SERVER_RUNTIME_VERSION
    payload = version_contract_payload(app)
    assert payload["features"]["playground_chat_window"] is True
    assert payload["features"]["playground_chat_records"] is True
    assert payload["features"]["playground_chat_running_records"] is True
    assert payload["features"]["model_capability_routing_guard"] is True
    assert payload["features"]["assistant_response_semantic_guard"] is True
    assert payload["features"]["assistant_response_semantic_recovery"] is True
    assert "playground-chat-v3" in payload["server"]["feature_revision"]
    assert "model-capability-routing-v2" in payload["server"]["feature_revision"]
    assert "response-semantic-recovery-v51" in payload["server"]["feature_revision"]
