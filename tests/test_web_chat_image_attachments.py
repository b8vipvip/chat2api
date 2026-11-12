from __future__ import annotations

import base64
import io
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch


def _register_and_create_key(client: TestClient, email: str) -> str:
    registered = client.post(
        "/api/user/register",
        headers={"Origin": "http://testserver"},
        json={
            "email": email,
            "password": "a-long-test-password",
            "display_name": email.split("@", 1)[0],
        },
    )
    assert registered.status_code == 200, registered.text
    created = client.post(
        "/api/user/keys",
        headers={"Origin": "http://testserver"},
        json={"name": "Web Chat image key"},
    )
    assert created.status_code == 200, created.text
    return created.json()["token"]


def _png_bytes() -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (2, 2), (30, 120, 90)).save(output, format="PNG")
    return output.getvalue()


def test_user_image_content_is_session_authenticated_and_account_owned(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    payload = _png_bytes()

    with TestClient(app) as client:
        assert client.get("/api/user/files/file_not_real123/content").status_code == 401

        token = _register_and_create_key(client, "image-owner@example.com")
        uploaded = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "reference.png",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(payload).decode("ascii"),
                "purpose": "vision",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["id"]

        preview = client.get(f"/api/user/files/{file_id}/content")
        assert preview.status_code == 200
        assert preview.content == payload
        assert preview.headers["content-type"].startswith("image/png")
        assert preview.headers["cache-control"] == "private, no-store"
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert preview.headers["cross-origin-resource-policy"] == "same-origin"

        # The same stored file is not visible after the browser session changes
        # to another account, even when the URL/file ID is known.
        client.post("/api/user/logout")
        _register_and_create_key(client, "different-user@example.com")
        denied = client.get(f"/api/user/files/{file_id}/content")
        assert denied.status_code == 404


def test_user_image_content_rejects_non_vision_and_non_image_files(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)

    with TestClient(app) as client:
        token = _register_and_create_key(client, "file-purpose@example.com")
        plain = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "not-image.bin",
                "mime_type": "application/octet-stream",
                "data_base64": base64.b64encode(b"not an image").decode("ascii"),
                "purpose": "chat2api",
            },
        )
        assert plain.status_code == 200, plain.text
        assert client.get(f"/api/user/files/{plain.json()['id']}/content").status_code == 404

        mislabeled = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "svg-is-not-allowed.svg",
                "mime_type": "image/svg+xml",
                "data_base64": base64.b64encode(b"<svg/>").decode("ascii"),
                "purpose": "vision",
            },
        )
        assert mislabeled.status_code == 200, mislabeled.text
        assert client.get(f"/api/user/files/{mislabeled.json()['id']}/content").status_code == 404


def test_user_can_delete_owned_vision_image_with_session_only(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    payload = _png_bytes()

    with TestClient(app) as client:
        token = _register_and_create_key(client, "image-delete@example.com")
        uploaded = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "delete-me.png",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(payload).decode("ascii"),
                "purpose": "vision",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["id"]

        missing_origin = client.delete(f"/api/user/files/{file_id}")
        assert missing_origin.status_code == 403
        cross_origin = client.delete(
            f"/api/user/files/{file_id}",
            headers={"Origin": "https://attacker.example"},
        )
        assert cross_origin.status_code == 403

        deleted = client.delete(
            f"/api/user/files/{file_id}",
            headers={"Origin": "http://testserver"},
        )
        assert deleted.status_code == 200
        assert deleted.json() == {"deleted": True}
        assert client.get(f"/api/user/files/{file_id}/content").status_code == 404
        assert client.delete(f"/api/user/files/{file_id}").status_code == 404


def test_web_chat_image_picker_and_client_javascript_are_wired(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)

    with TestClient(app) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert 'id="imageInput"' in page.text
        assert 'id="attachButton"' in page.text
        assert 'id="pendingImages"' in page.text
        assert "image/png,image/jpeg,image/webp,image/gif" in page.text

        script = client.get("/assets/chat2api-chat-client-v1.js")
        assert script.status_code == 200
        assert "/v1/files" in script.text
        assert 'purpose: "vision"' in script.text
        assert 'type: "image_url"' in script.text
        assert "/api/user/files/" in script.text
        assert 'method: "DELETE"' in script.text
        assert "MAX_IMAGES_PER_MESSAGE = 4" in script.text
        assert "MAX_IMAGE_BYTES = 10 * 1024 * 1024" in script.text
        assert "apiKeyId" in script.text

        node = shutil.which("node")
        if node:
            checked = subprocess.run(
                [node, "--check", "-"],
                input=script.text,
                text=True,
                capture_output=True,
                check=False,
                timeout=10,
            )
            assert checked.returncode == 0, checked.stdout + checked.stderr
