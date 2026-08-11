from concurrent.futures import ThreadPoolExecutor
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
from app.v13_patch import install_v13_patch


ROOT = Path(__file__).resolve().parents[1]


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def app_v13(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    install_v12_patch(app)
    install_v13_patch(app)
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


def test_v13_version_catalog_and_console(tmp_path: Path) -> None:
    with TestClient(app_v13(tmp_path)) as client:
        assert client.get("/").json()["version"] == "0.13.0"
        assert client.get("/healthz").json()["version"] == "0.13.0"
        catalog = client.get("/v1/models", headers=headers()).json()["data"]
        ids = [row["id"] for row in catalog]
        assert "gpt-5.6-sol" in ids
        assert "gpt-5.5" in ids
        assert "default" not in ids
        assert "chatgpt-web" not in ids
        text = {row["id"]: row for row in catalog}
        assert text["gpt-5.6-sol"]["reasoning_efforts"] == ["low", "medium", "high"]
        assert text["gpt-5.5"]["reasoning_labels"] == {"low": "极速", "medium": "中", "high": "高"}
        model = client.get("/v1/models/gpt-5.6-sol", headers=headers())
        assert model.status_code == 200
        assert model.json()["id"] == "gpt-5.6-sol"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.13.0"
        assert overview["capabilities"]["openai_responses_api"] is True
        assert overview["capabilities"]["canonical_text_models"] == ["gpt-5.6-sol", "gpt-5.5"]
        html = client.get("/admin").text
        assert "/assets/chat2api-v13.js" in html
        script = client.get("/assets/chat2api-v13.js")
        assert script.status_code == 200
        assert "reasoning_effort" in script.text
        assert "gpt-5.6-sol" in script.text and "gpt-5.5" in script.text


def test_removed_browser_aliases_are_rejected_before_routing(tmp_path: Path) -> None:
    with TestClient(app_v13(tmp_path)) as client:
        for model in ("default", "chatgpt-web", "o3"):
            response = client.post(
                "/v1/chat/completions",
                headers=headers(),
                json={"model": model, "messages": [{"role": "user", "content": "hello"}]},
            )
            assert response.status_code == 400
            assert response.json()["error"]["type"] == "invalid_request_error"


def test_chat_completions_sends_canonical_model_and_reasoning_to_extension(tmp_path: Path) -> None:
    with TestClient(app_v13(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=headers(),
                    json={
                        "model": "gpt-5.5",
                        "reasoning_effort": "high",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["options"]["model"] == "gpt-5.5"
                assert message["options"]["reasoning_level"] == "high"
                assert message["options"]["reasoning_effort"] == "high"
                complete_chat(websocket, message["request_id"], "chat-ok")
                response = future.result(timeout=5)
        assert response.status_code == 200
        assert response.json()["model"] == "gpt-5.5"
        assert response.json()["choices"][0]["message"]["content"] == "chat-ok"


def test_omitted_reasoning_preserves_current_page_strength(tmp_path: Path) -> None:
    with TestClient(app_v13(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=headers(),
                    json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "hello"}]},
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["options"]["model"] == "gpt-5.6-sol"
                assert "reasoning_level" not in message["options"]
                assert "reasoning_effort" not in message["options"]
                complete_chat(websocket, message["request_id"])
                assert future.result(timeout=5).status_code == 200


def test_responses_nonstream_maps_input_and_reasoning(tmp_path: Path) -> None:
    with TestClient(app_v13(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/responses",
                    headers=headers(),
                    json={
                        "model": "gpt-5.6-sol",
                        "reasoning": {"effort": "medium"},
                        "input": "Respond once",
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["options"]["model"] == "gpt-5.6-sol"
                assert message["options"]["reasoning_level"] == "medium"
                complete_chat(websocket, message["request_id"], "responses-ok")
                response = future.result(timeout=5)
        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "response"
        assert body["status"] == "completed"
        assert body["output_text"] == "responses-ok"
        assert body["output"][0]["content"][0]["text"] == "responses-ok"
        assert body["reasoning"]["effort"] == "medium"


def test_legacy_completions_nonstream_compatibility(tmp_path: Path) -> None:
    with TestClient(app_v13(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/completions",
                    headers=headers(),
                    json={"model": "gpt-5.5", "prompt": "legacy", "reasoning_effort": "low"},
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["options"]["reasoning_level"] == "instant"
                complete_chat(websocket, message["request_id"], "legacy-ok")
                response = future.result(timeout=5)
        assert response.status_code == 200
        body = response.json()
        assert body["object"] == "text_completion"
        assert body["choices"][0]["text"] == "legacy-ok"


def test_v13_source_contains_responses_stream_events_and_entry_order() -> None:
    source = (ROOT / "app" / "v13_patch.py").read_text(encoding="utf-8")
    for event in (
        "response.created",
        "response.output_item.added",
        "response.content_part.added",
        "response.output_text.delta",
        "response.output_text.done",
        "response.output_item.done",
        "response.completed",
    ):
        assert event in source
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v13_patch import install_v13_patch" in entry
    assert entry.index("install_v12_patch(app)") < entry.index("install_v13_patch(app)")
