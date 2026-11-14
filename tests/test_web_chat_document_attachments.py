from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="web-chat-document-master-key",
        CHAT2API_PAIRING_CODE="document-pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer web-chat-document-master-key"}


def _pair(client: TestClient) -> tuple[str, str]:
    response = client.post(
        "/api/extensions/register",
        headers={"X-Pairing-Code": "document-pair-code"},
        json={"name": "Document Worker", "version": "0.4.0"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    item = client.app.state.registry.clients[body["client_id"]]
    item.metadata.update({"chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True})
    return body["client_id"], body["token"]


def _upload_document(client: TestClient, filename: str, mime_type: str, payload: bytes) -> dict:
    response = client.post(
        "/v1/files",
        headers=_admin_headers(),
        json={
            "filename": filename,
            "mime_type": mime_type,
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "purpose": "assistants",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["purpose"] == "assistants"
    assert result["mime_type"] == mime_type
    return result


def test_chat_completion_file_content_part_is_forwarded_to_worker(tmp_path: Path) -> None:
    payload = b"Quarterly revenue increased by 12 percent."
    with TestClient(create_app(_settings(tmp_path))) as client:
        document = _upload_document(client, "quarterly-summary.txt", "text/plain", payload)
        client_id, token = _pair(client)
        with client.websocket_connect(f"/ws/extensions/{client_id}?token={token}") as websocket:
            websocket.receive_json()

            def request():
                return client.post(
                    "/v1/chat/completions",
                    headers=_admin_headers(),
                    json={
                        "model": "default",
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Summarize this document."},
                                {
                                    "type": "file",
                                    "file_id": document["id"],
                                    "filename": document["filename"],
                                    "mime_type": document["mime_type"],
                                    "size": document["bytes"],
                                },
                            ],
                        }],
                        "stream": False,
                    },
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(request)
                message = websocket.receive_json()
                assert message["type"] == "chat.request"
                assert message["attachments"][0]["file_id"] == document["id"]
                assert message["attachments"][0]["filename"] == "quarterly-summary.txt"
                assert message["attachments"][0]["mime_type"] == "text/plain"
                assert message["attachments"][0]["size"] == len(payload)
                # The paired Worker fetches the bytes through its authenticated
                # extension endpoint after receiving the scoped attachment spec.
                downloaded = client.get(
                    f"/api/extensions/files/{document['id']}",
                    params={
                        "request_id": message["request_id"],
                        "client_id": client_id,
                        "token": token,
                    },
                )
                assert downloaded.status_code == 200, downloaded.text
                assert downloaded.content == payload
                assert downloaded.headers["x-chat2api-filename"] == "quarterly-summary.txt"
                assert downloaded.headers["content-type"].startswith("text/plain")
                websocket.send_json({
                    "type": "chat.completed",
                    "request_id": message["request_id"],
                    "text": "Revenue increased by 12 percent.",
                })
                response = future.result(timeout=5)

        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == "Revenue increased by 12 percent."
        records = client.get("/api/admin/requests?limit=5", headers=_admin_headers()).json()["data"]
        assert records[0]["request_type"] == "multimodal"
        assert records[0]["attachments_count"] == 1


def test_chat_message_file_part_contributes_to_file_id_resolution() -> None:
    from app.models import ChatCompletionRequest

    body = ChatCompletionRequest.model_validate({
        "model": "default",
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "Summarize this."},
                {"type": "file", "file_id": "file_abcdefgh1234"},
            ],
        }],
    })
    assert body.all_file_ids() == ["file_abcdefgh1234"]


def test_document_content_part_rejects_non_assistants_and_unsupported_mime_files(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        image = client.post(
            "/v1/files", headers=_admin_headers(),
            json={
                "filename": "vision.png", "mime_type": "image/png",
                "data_base64": base64.b64encode(b"not-a-real-image").decode("ascii"),
                "purpose": "vision",
            },
        )
        assert image.status_code == 200, image.text
        unsupported = _upload_document(client, "unsafe.html", "text/html", b"<script>unsafe()</script>")
        for file_id in (image.json()["id"], unsupported["id"]):
            response = client.post(
                "/v1/chat/completions", headers=_admin_headers(),
                json={
                    "model": "default",
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Please inspect this file."},
                            {"type": "file", "file_id": file_id},
                        ],
                    }],
                },
            )
            assert response.status_code == 400, response.text
            assert "assistants document" in response.json()["detail"]


def test_document_content_part_rejects_corrupt_size_metadata(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        document = _upload_document(client, "notes.txt", "text/plain", b"valid bytes")
        item = client.app.state.file_store.get(document["id"])
        item.size += 1
        response = client.post(
            "/v1/chat/completions", headers=_admin_headers(),
            json={
                "model": "default",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize this."},
                        {"type": "file", "file_id": document["id"]},
                    ],
                }],
            },
        )
        assert response.status_code == 400, response.text
        assert "byte count" in response.json()["detail"]

def test_chat_completion_enforces_four_combined_file_input_limit(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        response = client.post(
            "/v1/chat/completions", headers=_admin_headers(),
            json={
                "model": "default",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Review these files."},
                        {"type": "file", "file_id": "file_document0001"},
                        {"type": "file", "file_id": "file_document0002"},
                        {"type": "file", "file_id": "file_document0003"},
                        {"type": "file", "file_id": "file_document0004"},
                        {"type": "file", "file_id": "file_document0005"},
                    ],
                }],
            },
        )
        assert response.status_code == 400, response.text
        assert "At most four image/file inputs" in response.json()["detail"]


def test_document_file_part_is_bound_to_the_uploading_api_key(tmp_path: Path) -> None:
    # create_app() is the API core; the user registration/key routes are
    # installed by the production entrypoint's existing user-console owner.
    # Install before TestClient starts because this owner adds middleware.
    app = create_app(_settings(tmp_path))
    install_user_console_v104_patch(app)
    assert any(
        getattr(route, "path", None) == "/api/user/register"
        and "POST" in (getattr(route, "methods", None) or set())
        for route in app.routes
    )
    with TestClient(app) as client:
        registered = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "doc-key-owner@example.test", "password": "document-key-password"},
        )
        assert registered.status_code == 200, registered.text
        key_a = client.post(
            "/api/user/keys",
            headers={"Origin": "http://testserver"},
            json={"name": "document uploader"},
        )
        assert key_a.status_code == 200, key_a.text
        token_a = key_a.json()["token"]
        payload = b"private document bytes"
        uploaded = client.post(
            "/v1/files",
            headers={"Authorization": "Bearer " + token_a},
            json={
                "filename": "private.txt", "mime_type": "text/plain",
                "data_base64": base64.b64encode(payload).decode("ascii"),
                "purpose": "assistants",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["id"]
        key_b = client.post(
            "/api/user/keys",
            headers={"Origin": "http://testserver"},
            json={"name": "different document key"},
        )
        assert key_b.status_code == 200, key_b.text
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer " + key_b.json()["token"]},
            json={
                "model": "default",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Summarize this document."},
                        {"type": "file", "file_id": file_id},
                    ],
                }],
            },
        )
        assert response.status_code == 403, response.text
        assert "belongs to another API key" in response.json()["detail"]

        deleted = client.delete(
            f"/api/user/files/{file_id}",
            headers={"Origin": "http://testserver"},
        )
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"deleted": True}

def test_chat_completion_preflights_combined_file_and_inline_image_count(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        documents = [
            _upload_document(client, f"document-{index}.txt", "text/plain", f"doc-{index}".encode())
            for index in range(4)
        ]
        before = set(client.app.state.file_store.items)
        response = client.post(
            "/v1/chat/completions", headers=_admin_headers(),
            json={
                "model": "default",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Review these attachments."},
                        *[{"type": "file", "file_id": item["id"]} for item in documents],
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,not-even-decoded"}},
                    ],
                }],
            },
        )
        assert response.status_code == 400, response.text
        assert "At most four image/file inputs" in response.json()["detail"]
        assert set(client.app.state.file_store.items) == before

def test_chat_completion_enforces_twenty_mib_combined_attachment_limit(tmp_path: Path) -> None:
    with TestClient(create_app(_settings(tmp_path))) as client:
        documents = [
            _upload_document(client, f"document-{index}.txt", "text/plain", f"doc-{index}".encode())
            for index in range(2)
        ]
        for document in documents:
            client.app.state.file_store.get(document["id"]).size = 11 * 1024 * 1024
        response = client.post(
            "/v1/chat/completions", headers=_admin_headers(),
            json={
                "model": "default",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Review these documents."},
                        *[{"type": "file", "file_id": item["id"]} for item in documents],
                    ],
                }],
            },
        )
        assert response.status_code == 400, response.text
        assert "20 MiB" in response.json()["detail"]