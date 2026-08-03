from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
    )


def pair(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Integration Chrome", "version": "0.1.0"},
    )
    assert response.status_code == 200
    return response.json()["client_id"], response.json()["token"]


def test_non_stream_bridge_round_trip(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            assert websocket.receive_json()["type"] == "server.hello"
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    client.post,
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer test-key"},
                    json={
                        "model": "chatgpt-web",
                        "client_id": client_id,
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )
                request = websocket.receive_json()
                assert request["type"] == "chat.request"
                assert request["prompt"] == "hello"
                websocket.send_json({"type": "chat.delta", "request_id": request["request_id"], "delta": "Hi"})
                websocket.send_json({"type": "chat.completed", "request_id": request["request_id"], "text": "Hi there"})
                response = future.result(timeout=5)
            assert response.status_code == 200
            assert response.json()["choices"][0]["message"]["content"] == "Hi there"


def test_stream_bridge_round_trip(tmp_path: Path) -> None:
    with TestClient(create_app(make_settings(tmp_path))) as client:
        client_id, token = pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def make_stream_request() -> tuple[int, list[str]]:
                with client.stream(
                    "POST",
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer test-key"},
                    json={
                        "model": "chatgpt-web",
                        "client_id": client_id,
                        "messages": [{"role": "user", "content": "stream"}],
                        "stream": True,
                    },
                ) as response:
                    return response.status_code, list(response.iter_lines())

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(make_stream_request)
                request = websocket.receive_json()
                websocket.send_json({"type": "chat.delta", "request_id": request["request_id"], "delta": "A"})
                websocket.send_json({"type": "chat.delta", "request_id": request["request_id"], "delta": "B"})
                websocket.send_json({"type": "chat.completed", "request_id": request["request_id"], "text": "ABC"})
                status_code, lines = future.result(timeout=5)
            assert status_code == 200
            joined = "\n".join(lines)
            assert '"content": "A"' in joined
            assert '"content": "B"' in joined
            assert '"content": "C"' in joined
            assert "data: [DONE]" in joined
