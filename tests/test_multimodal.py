import base64
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


def pair(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "pair-code"},
        json={"name": "Chrome", "version": "0.4.0"},
    )
    assert response.status_code == 200
    body = response.json()
    return body["client_id"], body["token"]


def upload(client: TestClient, name: str, mime: str, payload: bytes, purpose: str) -> dict:
    response = client.post(
        "/v1/files",
        headers=admin_headers(),
        json={
            "filename": name,
            "mime_type": mime,
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "purpose": purpose,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_file_upload_is_forwarded_to_chat_and_downloadable_by_extension(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        image = upload(client, "vision.png", "image/png", b"fake-png-payload", "vision")
        client_id, token = pair(client)

        extension_download = client.get(
            f"/api/extensions/files/{image['id']}?client_id={client_id}&token={token}"
        )
        assert extension_download.status_code == 200
        assert extension_download.content == b"fake-png-payload"
        assert extension_download.headers["content-type"].startswith("image/png")

        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=admin_headers(),
                    json={
                        "model": "default",
                        "messages": [{"role": "user", "content": "describe image"}],
                        "attachments": [{"file_id": image["id"]}],
                        "stream": False,
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["attachments"][0]["file_id"] == image["id"]
                assert message["attachments"][0]["mime_type"] == "image/png"
                websocket.send_json(
                    {
                        "type": "chat.diagnostics",
                        "request_id": message["request_id"],
                        "diagnostics": {"attachments_count": 1, "attachment_prepare_ms": 123.4},
                    }
                )
                websocket.send_json(
                    {"type": "chat.completed", "request_id": message["request_id"], "text": "an image"}
                )
                response = future.result(timeout=5)

        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "an image"
        records = client.get("/api/admin/requests?limit=5", headers=admin_headers()).json()["data"]
        assert records[0]["request_type"] == "multimodal"
        assert records[0]["attachments_count"] == 1
        assert records[0]["timings"]["attachment_prepare_ms"] == 123.4


def test_inline_data_url_image_is_materialized_as_attachment(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        client_id, token = pair(client)
        data_url = "data:image/png;base64," + base64.b64encode(b"inline-image").decode("ascii")
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=admin_headers(),
                    json={
                        "model": "default",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": "what is this"},
                                    {"type": "image_url", "image_url": {"url": data_url}},
                                ],
                            }
                        ],
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert len(message["attachments"]) == 1
                assert message["attachments"][0]["file_id"].startswith("file_")
                websocket.send_json({"type": "chat.completed", "request_id": message["request_id"], "text": "ok"})
                response = future.result(timeout=5)
        assert response.status_code == 200


def test_remote_image_url_is_rejected_without_ssrf_fetch(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        response = client.post(
            "/v1/chat/completions",
            headers=admin_headers(),
            json={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "describe"},
                            {"type": "image_url", "image_url": {"url": "https://example.com/private.png"}},
                        ],
                    }
                ]
            },
        )
        assert response.status_code == 400
        assert "Remote image URLs" in response.json()["detail"]


def test_gpt_image_route_round_trip(tmp_path: Path) -> None:
    with TestClient(create_app(settings(tmp_path))) as client:
        reference = upload(client, "reference.png", "image/png", b"reference", "image-reference")
        client_id, token = pair(client)
        generated = base64.b64encode(b"generated-image-bytes").decode("ascii")
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/images/generations",
                    headers=admin_headers(),
                    json={
                        "model": "gpt-image",
                        "prompt": "make a test image",
                        "response_format": "b64_json",
                        "attachments": [{"file_id": reference["id"]}],
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "image.request"
                assert message["attachments"][0]["file_id"] == reference["id"]
                websocket.send_json(
                    {
                        "type": "image.diagnostics",
                        "request_id": message["request_id"],
                        "diagnostics": {"route": "chatgpt-images", "tab_ready_ms": 80},
                    }
                )
                websocket.send_json({"type": "image.started", "request_id": message["request_id"]})
                websocket.send_json(
                    {
                        "type": "image.completed",
                        "request_id": message["request_id"],
                        "images": [{"b64_json": generated, "mime_type": "image/png"}],
                    }
                )
                response = future.result(timeout=5)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model"] == "gpt-image"
        assert body["data"][0]["b64_json"] == generated
        assert body["chat2api"]["diagnostics"]["route"] == "chatgpt-images"
        records = client.get("/api/admin/requests?model=gpt-image", headers=admin_headers()).json()["data"]
        assert records[0]["request_type"] == "image_generation"
        assert records[0]["attachments_count"] == 1


def test_test_report_is_persisted_and_readable(tmp_path: Path) -> None:
    report = {
        "run_id": "testrun_1",
        "test_type": "all",
        "status": "warning",
        "model": "default",
        "duration_ms": 1200,
        "summary": "3 passed, 2 skipped",
        "results": [{"kind": "text", "status": "passed"}, {"kind": "voice_generation", "status": "skipped"}],
        "quality": {"bugs": [], "latency_warnings": []},
    }
    with TestClient(create_app(settings(tmp_path))) as client:
        saved = client.post("/api/admin/tests", headers=admin_headers(), json=report)
        assert saved.status_code == 200
        listing = client.get("/api/admin/tests", headers=admin_headers()).json()["data"]
        assert listing[0]["run_id"] == "testrun_1"
        detail = client.get("/api/admin/tests/testrun_1", headers=admin_headers())
        assert detail.status_code == 200
        assert detail.json()["results"][1]["status"] == "skipped"
