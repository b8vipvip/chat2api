from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def pair(client: TestClient) -> tuple[str, str]:
    response = client.post("/api/extensions/register", headers={"X-Pairing-Code": "pair-code"}, json={"name": "Chrome", "version": "0.4.0"})
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def test_desktop_runtime_is_removed(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/api/desktop/bootstrap").status_code == 404
        health = client.get("/healthz").json()
        assert "online_desktop_agents" not in health


def test_dynamic_model_catalog_from_extension_status(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "extension.status", "metadata": {"models": [{"id": "gpt-5.6-sol-high", "label": "GPT-5.6 Sol / High", "family": "gpt-5.6-sol", "reasoning": "high", "capabilities": ["text"], "selected": True}], "current_model": "gpt-5.6-sol-high"}})
            response = client.get("/v1/models", headers={"Authorization": "Bearer test-key"})
            assert response.status_code == 200
            models = {item["id"]: item for item in response.json()["data"]}
            assert "default" in models
            assert "chatgpt-web" in models
            assert "gpt-image" in models
            assert "vision" in models["default"]["capabilities"]
            assert models["gpt-5.6-sol-high"]["clients"] == [client_id]
            assert models["gpt-5.6-sol-high"]["selected_on"] == client_id


def complete_request(websocket, message: dict, text: str = "done") -> None:
    websocket.send_json({"type": "chat.completed", "request_id": message["request_id"], "text": text})


def test_requested_model_is_forwarded_to_extension(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "extension.status", "metadata": {"models": [{"id": "gpt-5.6-sol-high", "label": "High"}]}})

            def make_request():
                return client.post("/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json={"model": "gpt-5.6-sol-high", "messages": [{"role": "user", "content": "hello"}]})

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(make_request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["options"]["model"] == "gpt-5.6-sol-high"
                complete_request(websocket, message)
                response = future.result(timeout=5)
            assert response.status_code == 200
            body = response.json()
            assert body["model"] == "gpt-5.6-sol-high"
            assert body["choices"][0]["message"]["content"] == "done"
            assert body["usage"]["total_tokens"] > 0
            assert body["chat2api"]["token_usage"]["estimated"] is True


def test_stale_catalog_does_not_block_request_driven_model_selection(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()
            websocket.send_json({"type": "extension.status", "metadata": {"models": [{"id": "gpt-5.6-sol-high", "label": "High"}]}})

            def make_request():
                return client.post("/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json={"model": "gpt-5.5", "messages": [{"role": "user", "content": "hello"}]})

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(make_request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["options"]["model"] == "gpt-5.5"
                complete_request(websocket, message, "selected")
                response = future.result(timeout=5)
            assert response.status_code == 200
            assert response.json()["choices"][0]["message"]["content"] == "selected"


def test_browser_diagnostics_are_returned_and_visible_in_admin_history(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def make_request():
                return client.post("/v1/chat/completions", headers={"Authorization": "Bearer test-key"}, json={"model": "gpt-5.6-sol-high", "messages": [{"role": "user", "content": "hello diagnostics"}]})

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(make_request)
                message = websocket.receive_json()
                websocket.send_json({"type": "chat.diagnostics", "request_id": message["request_id"], "diagnostics": {"requested_model": "gpt-5.6-sol-high", "actual_family": "gpt-5.6-sol", "actual_reasoning": "high", "actual_model": "gpt-5.6-sol-high", "zero_op": True, "model_selection_ms": 0}})
                websocket.send_json({"type": "chat.started", "request_id": message["request_id"]})
                websocket.send_json({"type": "chat.delta", "request_id": message["request_id"], "delta": "ok"})
                complete_request(websocket, message, "ok")
                response = future.result(timeout=5)

            body = response.json()
            assert body["chat2api"]["diagnostics"]["actual_model"] == "gpt-5.6-sol-high"
            assert body["chat2api"]["diagnostics"]["zero_op"] is True
            assert body["chat2api"]["timings"]["first_token_ms"] is not None

            overview = client.get("/api/admin/overview", headers={"Authorization": "Bearer test-key"})
            assert overview.status_code == 200
            history = overview.json()["recent_requests"]
            assert history[0]["diagnostics"]["actual_reasoning"] == "high"
            assert history[0]["usage"]["estimated"] is True


def test_admin_page_is_public_shell_but_data_requires_key(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/admin").status_code == 200
        assert client.get("/api/admin/overview").status_code == 401
