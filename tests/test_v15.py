from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v10_patch import install_v10_patch
from app.v11_patch import install_v11_patch
from app.v12_patch import install_v12_patch
from app.v13_patch import install_v13_patch
from app.v14_patch import install_v14_patch
from app.v15_patch import install_v15_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.voice_patch import install_voice_patch


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


def app_v15(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    install_v12_patch(app)
    install_v13_patch(app)
    install_v14_patch(app)
    install_v15_patch(app)
    return app


def pair_extension(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.7.3"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def complete_chat(websocket, request_id: str, text: str = "ok") -> None:
    websocket.send_json({"type": "chat.started", "request_id": request_id})
    websocket.send_json({"type": "chat.completed", "request_id": request_id, "text": text})


def test_v15_version_catalog_and_docs_advertise_medium_default(tmp_path: Path) -> None:
    with TestClient(app_v15(tmp_path)) as client:
        health = client.get("/healthz").json()
        assert health["version"] == "0.15.0"

        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.15.0"
        assert overview["capabilities"]["default_reasoning_effort"] == "medium"
        assert overview["capabilities"]["default_reasoning_label"] == "中"
        assert overview["capabilities"]["omitted_reasoning_is_deterministic"] is True

        catalog = client.get("/v1/models", headers=headers()).json()["data"]
        text_models = {row["id"]: row for row in catalog if row["id"] in {"gpt-5.6-sol", "gpt-5.5"}}
        assert set(text_models) == {"gpt-5.6-sol", "gpt-5.5"}
        for row in text_models.values():
            assert row["default_reasoning_effort"] == "medium"
            assert row["default_reasoning_label"] == "中"

        script = client.get("/assets/chat2api-v15.js")
        assert script.status_code == 200
        assert "v0.15.0" in script.text
        assert "medium" in script.text
        assert "省略该参数时统一" in script.text

    docs = (ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    assert "未传 `reasoning_effort`" in docs
    assert "统一归一为 `medium`" in docs
    assert "不得继承当前页面已有的推理强度" in docs


def test_chat_completions_omitted_reasoning_defaults_to_medium(tmp_path: Path) -> None:
    with TestClient(app_v15(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=headers(),
                    json={
                        "model": "gpt-5.6-sol",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["options"]["model"] == "gpt-5.6-sol"
                assert message["options"]["reasoning_level"] == "medium"
                assert message["options"]["reasoning_effort"] == "medium"
                complete_chat(websocket, message["request_id"], "chat-medium")
                response = future.result(timeout=5)

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "chat-medium"


def test_responses_omitted_reasoning_defaults_to_medium_and_reports_it(tmp_path: Path) -> None:
    with TestClient(app_v15(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/responses",
                    headers=headers(),
                    json={"model": "gpt-5.5", "input": "hello"},
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["options"]["model"] == "gpt-5.5"
                assert message["options"]["reasoning_level"] == "medium"
                assert message["options"]["reasoning_effort"] == "medium"
                complete_chat(websocket, message["request_id"], "responses-medium")
                response = future.result(timeout=5)

        assert response.status_code == 200
        body = response.json()
        assert body["output_text"] == "responses-medium"
        assert body["reasoning"]["effort"] == "medium"
        assert body["chat2api"]["compatibility"]["reasoning_effort"] == "medium"


def test_legacy_completions_omitted_reasoning_defaults_to_medium(tmp_path: Path) -> None:
    with TestClient(app_v15(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/completions",
                    headers=headers(),
                    json={"model": "gpt-5.6-sol", "prompt": "legacy"},
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["options"]["reasoning_level"] == "medium"
                assert message["options"]["reasoning_effort"] == "medium"
                complete_chat(websocket, message["request_id"], "legacy-medium")
                response = future.result(timeout=5)

        assert response.status_code == 200
        assert response.json()["choices"][0]["text"] == "legacy-medium"


def test_explicit_reasoning_still_overrides_default(tmp_path: Path) -> None:
    with TestClient(app_v15(tmp_path)) as client:
        client_id, token = pair_extension(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=headers(),
                    json={
                        "model": "gpt-5.5",
                        "reasoning_effort": "low",
                        "messages": [{"role": "user", "content": "hello"}],
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["options"]["reasoning_level"] == "instant"
                assert message["options"]["reasoning_effort"] == "low"
                complete_chat(websocket, message["request_id"])
                assert future.result(timeout=5).status_code == 200


def test_production_entry_installs_v15_after_v14() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v15_patch import install_v15_patch" in source
    assert source.index("install_v14_patch(app)") < source.index("install_v15_patch(app)")
