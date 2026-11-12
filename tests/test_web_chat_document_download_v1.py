"""W-05 document download: user session, file owner, safe headers and bytes."""
import base64
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_file_download_api import install_web_chat_file_download_api


def make_app(data_dir: Path):
    app = create_app(Settings(CHAT2API_API_KEY="w05-test-key", CHAT2API_DATA_DIR=str(data_dir)))
    install_user_console_v104_patch(app)
    install_web_chat_file_download_api(app)
    install_web_chat_file_download_api(app)
    return app


def register_and_create_key(client: TestClient, email: str):
    registered = client.post(
        "/api/user/register",
        headers={"Origin": "http://testserver"},
        json={"email": email, "password": "secure-password-123"},
    )
    assert registered.status_code == 200, registered.text
    created = client.post(
        "/api/user/keys",
        headers={"Origin": "http://testserver"},
        json={"name": "Web Chat document key"},
    )
    assert created.status_code == 200, created.text
    return created.json()["token"]


def upload(client: TestClient, token: str, filename: str, mime_type: str, data: bytes):
    response = client.post(
        "/v1/files",
        headers={"Authorization": "Bearer " + token},
        json={
            "filename": filename,
            "mime_type": mime_type,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "purpose": "chat2api",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"]


def test_owner_can_download_exact_bytes_as_attachment_with_safe_headers(tmp_path):
    app = make_app(tmp_path)
    payload = b"Chat2API document payload\n\x00\xff"
    with TestClient(app) as client:
        token = register_and_create_key(client, "document-owner@example.org")
        file_id = upload(client, token, "合同<2026>.html", "text/html", payload)
        response = client.get(f"/api/user/chat/files/{file_id}/download")
        assert response.status_code == 200
        assert response.content == payload
        assert response.headers["content-type"].startswith("text/html")
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert response.headers["cross-origin-resource-policy"] == "same-origin"
        assert response.headers["referrer-policy"] == "no-referrer"
        disposition = response.headers["content-disposition"]
        assert disposition.startswith("attachment;")
        assert "filename*=UTF-8''" in disposition
        assert "\r" not in disposition and "\n" not in disposition
        assert ".." not in disposition


def test_other_account_unknown_and_missing_file_content_are_hidden(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        owner_token = register_and_create_key(client, "file-owner@example.org")
        file_id = upload(client, owner_token, "notes.txt", "text/plain", b"private")
        client.post("/api/user/logout")
        register_and_create_key(client, "other-owner@example.org")
        assert client.get(f"/api/user/chat/files/{file_id}/download").status_code == 404
        assert client.get("/api/user/chat/files/file_notreal123/download").status_code == 404
        assert client.get("/api/user/chat/files/not-a-file/download").status_code == 404


def test_download_requires_user_session_and_missing_disk_bytes_are_404(tmp_path):
    app = make_app(tmp_path)
    with TestClient(app) as client:
        assert client.get("/api/user/chat/files/file_notreal123/download").status_code == 401
        token = register_and_create_key(client, "missing-bytes@example.org")
        file_id = upload(client, token, "notes.txt", "text/plain", b"private")
        stored = app.state.file_store.get(file_id)
        (app.state.file_store.root / stored.path).unlink()
        assert client.get(f"/api/user/chat/files/{file_id}/download").status_code == 404
