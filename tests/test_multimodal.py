import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.attachment_download_v82_patch import install_attachment_download_v82_patch


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
    client_id = body["client_id"]
    # Multimodal round-trip tests model an authenticated, usable ChatGPT Worker.
    # v137 intentionally rejects transport-online registrations until positive
    # login + composer evidence exists, so make that fixture state explicit.
    item = client.app.state.registry.clients[client_id]
    item.metadata.update(
        {
            "chatgpt_login_state": "ready",
            "chatgpt_login_composer_ready": True,
        }
    )
    return client_id, body["token"]


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
    app = create_app(settings(tmp_path))
    install_attachment_download_v82_patch(app)
    with TestClient(app) as client:
        image = upload(client, "vision.png", "image/png", b"fake-png-payload", "vision")
        client_id, token = pair(client)

        unrelated = upload(client, "other.png", "image/png", b"other-payload", "vision")

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
                assert message["attachments"][0]["request_id"] == message["request_id"]

                # Worker access is request-scoped: missing, unrelated request,
                # wrong Worker and a same-owner but unattached file all fail closed.
                missing_request = client.get(
                    f"/api/extensions/files/{image['id']}?client_id={client_id}&token={token}"
                )
                assert missing_request.status_code == 422
                wrong_request = client.get(
                    f"/api/extensions/files/{image['id']}?request_id=req_unrelated_000001"
                    f"&client_id={client_id}&token={token}"
                )
                assert wrong_request.status_code == 404
                wrong_worker_id, wrong_worker_token = pair(client)
                wrong_worker = client.get(
                    f"/api/extensions/files/{image['id']}?request_id={message['request_id']}"
                    f"&client_id={wrong_worker_id}&token={wrong_worker_token}"
                )
                assert wrong_worker.status_code == 404
                unattached = client.get(
                    f"/api/extensions/files/{unrelated['id']}?request_id={message['request_id']}"
                    f"&client_id={client_id}&token={token}"
                )
                assert unattached.status_code == 404

                extension_download = client.get(
                    f"/api/extensions/files/{image['id']}?request_id={message['request_id']}"
                    f"&client_id={client_id}&token={token}"
                )
                assert extension_download.status_code == 200
                assert extension_download.content == b"fake-png-payload"
                assert extension_download.headers["content-type"].startswith("image/png")
                assert extension_download.headers["x-chat2api-attachment-revision"] == "83"

                # A replacement socket with the same paired client_id must not
                # inherit the original dispatch's still-running attachment grant.
                active_state = app.state.broker.requests[message["request_id"]]
                original_socket = app.state.registry.sockets[client_id]
                assert active_state.dispatch_socket is original_socket
                app.state.registry.sockets[client_id] = object()
                replaced_socket = client.get(
                    f"/api/extensions/files/{image['id']}?request_id={message['request_id']}"
                    f"&client_id={client_id}&token={token}"
                )
                assert replaced_socket.status_code == 404
                app.state.registry.sockets[client_id] = original_socket

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
        assert records[0]["input_file_ids"] == [image["id"]]
        assert records[0]["timings"]["attachment_prepare_ms"] == 123.4



def test_worker_attachment_grant_requires_a_live_nonterminal_broker_lease(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    install_attachment_download_v82_patch(app)
    with TestClient(app) as client:
        image = upload(client, "stale-grant.png", "image/png", b"private-payload", "vision")
        client_id, token = pair(client)
        request_id = "req_stale_worker_grant_0001"
        asyncio.run(app.state.telemetry.upsert({
            "request_id": request_id,
            "client_id": client_id,
            "api_key_id": "master",
            "status": "running",
            "input_file_ids": [image["id"]],
        }))

        # Telemetry can outlive a failed dispatch or a process-local lease.
        # A running row alone is never sufficient authority for a Worker read.
        assert app.state.broker.requests.get(request_id) is None
        response = client.get(
            f"/api/extensions/files/{image['id']}?request_id={request_id}"
            f"&client_id={client_id}&token={token}"
        )
        assert response.status_code == 404
        assert response.content != b"private-payload"

        # Even if a stale telemetry row remains "running", a terminal Broker
        # state revokes the download grant before the telemetry projection catches up.
        active_state = asyncio.run(app.state.broker.create(request_id, client_id))
        active_state.terminal_event_type = "chat.completed"
        terminal_response = client.get(
            f"/api/extensions/files/{image['id']}?request_id={request_id}"
            f"&client_id={client_id}&token={token}"
        )
        assert terminal_response.status_code == 404
        asyncio.run(app.state.broker.release(request_id))


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
                assert message["attachments"][0]["request_id"] == message["request_id"]
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
        assert records[0]["input_file_ids"] == [reference["id"]]


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


def test_admin_request_media_rejects_file_owned_by_another_api_key(tmp_path: Path) -> None:
    app = create_app(settings(tmp_path))
    async def seed():
        item = await app.state.file_store.create(
            filename="foreign-generated.png",
            data_base64=base64.b64encode(b"not-an-image").decode("ascii"),
            mime_type="image/png",
            owner_key_id="key_file_owner",
            purpose="generated-output",
        )
        await app.state.telemetry.upsert({
            "request_id": "req_media_owner_mismatch_0001",
            "api_key_id": "key_request_owner",
            "status": "completed",
            "response_media": [{"file_id": item.file_id}],
        })
        return item
    item = asyncio.run(seed())
    with TestClient(app) as client:
        response = client.get(
            f"/api/admin/requests/req_media_owner_mismatch_0001/media/{item.file_id}",
            headers=admin_headers(),
        )
        assert response.status_code == 404
