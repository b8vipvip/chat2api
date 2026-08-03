from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="test-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_DATA_DIR=tmp_path,
    )


def test_health(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


def test_api_key_required(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        assert client.get("/v1/models").status_code == 401
        assert client.get("/v1/models", headers={"Authorization": "Bearer test-key"}).status_code == 200


def test_pairing_and_offline_completion(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        paired = client.post(
            "/api/extensions/register",
            headers={"X-Pairing-Code": "pair-code"},
            json={"name": "Test Chrome", "version": "0.1.0"},
        )
        assert paired.status_code == 200
        client_id = paired.json()["client_id"]
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer test-key"},
            json={"model": "chatgpt-web", "client_id": client_id, "messages": [{"role": "user", "content": "hello"}]},
        )
        assert response.status_code == 503
