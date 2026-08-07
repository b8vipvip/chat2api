from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def create_managed_key(client: TestClient, name: str = "Mobile App") -> tuple[dict, str]:
    response = client.post(
        "/api/admin/keys",
        headers=admin_headers(),
        json={"name": name, "expires_in_days": 30},
    )
    assert response.status_code == 200
    body = response.json()
    return body["key"], body["token"]


def pair_extension(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.3.5"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def test_managed_api_key_lifecycle_and_admin_boundary(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        key, token = create_managed_key(client)
        assert token.startswith("sk-chat2api-")
        assert key["key_id"].startswith("key_")
        assert key["enabled"] is True

        listing = client.get("/api/admin/keys", headers=admin_headers())
        assert listing.status_code == 200
        serialized = listing.text
        assert token not in serialized
        assert "token_hash" not in serialized

        managed_headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/v1/models", headers=managed_headers).status_code == 200
        assert client.get("/api/admin/overview", headers=managed_headers).status_code == 403
        assert client.get("/api/desktop/bootstrap", headers=managed_headers).status_code == 403

        disabled = client.patch(
            f"/api/admin/keys/{key['key_id']}",
            headers=admin_headers(),
            json={"enabled": False},
        )
        assert disabled.status_code == 200
        assert client.get("/v1/models", headers=managed_headers).status_code == 401

        enabled = client.patch(
            f"/api/admin/keys/{key['key_id']}",
            headers=admin_headers(),
            json={"enabled": True, "name": "Renamed App"},
        )
        assert enabled.status_code == 200
        assert client.get("/v1/models", headers=managed_headers).status_code == 200

        revoked = client.delete(f"/api/admin/keys/{key['key_id']}", headers=admin_headers())
        assert revoked.status_code == 200
        assert revoked.json()["key"]["revoked_at"]
        assert client.get("/v1/models", headers=managed_headers).status_code == 401


def test_request_record_is_attributed_to_managed_key_and_filterable(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        key, api_token = create_managed_key(client, "Integration Test")
        client_id, extension_token = pair_extension(client)

        with client.websocket_connect(f"/ws/extensions/{client_id}?token={extension_token}") as websocket:
            hello = websocket.receive_json()
            assert hello["type"] == "server.hello"

            def make_request():
                return client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_token}"},
                    json={
                        "model": "default",
                        "messages": [{"role": "user", "content": "hello record"}],
                        "stream": False,
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(make_request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                request_id = message["request_id"]
                websocket.send_json(
                    {
                        "type": "chat.diagnostics",
                        "request_id": request_id,
                        "diagnostics": {
                            "actual_model": "gpt-5.6-sol-high",
                            "zero_op": True,
                            "model_selection_ms": 0,
                        },
                    }
                )
                websocket.send_json(
                    {
                        "type": "chat.completed",
                        "request_id": request_id,
                        "text": "recorded",
                    }
                )
                response = future.result(timeout=5)

            assert response.status_code == 200
            payload = response.json()
            assert payload["chat2api"]["api_key"]["key_id"] == key["key_id"]

        records = client.get(
            f"/api/admin/requests?key_id={key['key_id']}&status=completed",
            headers=admin_headers(),
        )
        assert records.status_code == 200
        body = records.json()
        assert body["total"] == 1
        row = body["data"][0]
        assert row["api_key_id"] == key["key_id"]
        assert row["api_key_name"] == "Integration Test"
        assert row["prompt_chars"] > 0
        assert row["completion_chars"] == len("recorded")
        assert row["usage"]["total_tokens"] > 0

        detail = client.get(f"/api/admin/requests/{row['request_id']}", headers=admin_headers())
        assert detail.status_code == 200
        assert detail.json()["request_id"] == row["request_id"]


def test_console_developer_docs_and_playground_routes(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        admin = client.get("/admin")
        assert admin.status_code == 200
        assert "API Key" in admin.text
        assert "请求记录" in admin.text
        assert "开发文档" in admin.text
        assert "测试场" in admin.text
        assert "/v1/chat/completions" in admin.text

        developers = client.get("/developers")
        assert developers.status_code == 200
        assert "const INITIAL_VIEW='docs'" in developers.text

        root = client.get("/").json()
        assert root["version"] == "0.4.0"
        assert root["developers"] == "/developers"
