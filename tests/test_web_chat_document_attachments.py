from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


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
    return response.json()


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
