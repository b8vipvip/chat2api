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
        import hashlib
        digest = hashlib.sha256(payload).hexdigest()
        assert preview.headers["etag"] == f'"sha256-{digest}"'
        assert preview.headers["x-chat2api-content-sha256"] == digest
        assert preview.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in preview.headers["vary"].split(",")}
        assert preview.headers["x-content-type-options"] == "nosniff"
        assert preview.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert preview.headers["cross-origin-resource-policy"] == "same-origin"

        # The same stored file is not visible after the browser session changes
        # to another account, even when the URL/file ID is known.
        client.post("/api/user/logout")
        _register_and_create_key(client, "different-user@example.com")
        denied = client.get(f"/api/user/files/{file_id}/content")
        assert denied.status_code == 404
        denied_delete = client.delete(
            f"/api/user/files/{file_id}",
            headers={"Origin": "http://testserver"},
        )
        assert denied_delete.status_code == 404


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


def test_user_image_preview_rejects_invalid_raster_bytes(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    with TestClient(app) as client:
        token = _register_and_create_key(client, "invalid-raster@example.com")
        uploaded = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "invalid.png", "mime_type": "image/png",
                "data_base64": base64.b64encode(b"not a raster image").decode("ascii"),
                "purpose": "vision",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        preview = client.get(f"/api/user/files/{uploaded.json()['id']}/content")
        assert preview.status_code == 415
        assert preview.headers["cache-control"] == "private, no-store"

def test_legacy_file_index_backfills_sha256_on_first_read(tmp_path: Path) -> None:
    import asyncio
    import hashlib
    import json

    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    payload = _png_bytes()

    with TestClient(app) as client:
        token = _register_and_create_key(client, "legacy-image-index@example.com")
        uploaded = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "legacy.png",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(payload).decode("ascii"),
                "purpose": "vision",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["id"]

        index_path = app.state.file_store.index_path
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for row in index["files"]:
            if row["file_id"] == file_id:
                row.pop("sha256", None)
        index_path.write_text(json.dumps(index), encoding="utf-8")
        asyncio.run(app.state.file_store.load())
        legacy_item = app.state.file_store.get(file_id)
        assert legacy_item is not None and legacy_item.sha256 == ""

        preview = client.get(f"/api/user/files/{file_id}/content")
        assert preview.status_code == 200
        digest = hashlib.sha256(payload).hexdigest()
        assert preview.headers["x-chat2api-content-sha256"] == digest
        assert legacy_item.sha256 == digest
        persisted = json.loads(index_path.read_text(encoding="utf-8"))
        stored_row = next(row for row in persisted["files"] if row["file_id"] == file_id)
        assert stored_row["sha256"] == digest


def test_user_image_preview_rejects_same_length_at_rest_corruption(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-image-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    payload = _png_bytes()

    with TestClient(app) as client:
        token = _register_and_create_key(client, "corrupt-raster@example.com")
        uploaded = client.post(
            "/v1/files",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "filename": "integrity.png",
                "mime_type": "image/png",
                "data_base64": base64.b64encode(payload).decode("ascii"),
                "purpose": "vision",
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        file_id = uploaded.json()["id"]
        item = app.state.file_store.get(file_id)
        assert item is not None and item.sha256
        disk_path = app.state.file_store.root / item.path
        tampered = payload[:-1] + bytes([payload[-1] ^ 0x01])
        assert len(tampered) == len(payload)
        disk_path.write_bytes(tampered)

        response = client.get(f"/api/user/files/{file_id}/content")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in response.headers["vary"].split(",")}


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
        malformed_origin = client.delete(
            f"/api/user/files/{file_id}",
            headers={"Origin": "http://[::1"},
        )
        assert malformed_origin.status_code == 403
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
        assert deleted.headers["cache-control"] == "private, no-store"
        assert "cookie" in {value.strip().lower() for value in deleted.headers["vary"].split(",")}
        assert deleted.json() == {"deleted": True}
        assert client.get(f"/api/user/files/{file_id}/content").status_code == 404
        assert client.delete(
            f"/api/user/files/{file_id}",
            headers={"Origin": "http://testserver"},
        ).status_code == 404


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
        assert "orphanedFileIds" in script.text
        assert "remainingFileIds" in script.text


        drop_script = client.get("/assets/chat2api-image-drop-v1.js")
        assert drop_script.status_code == 200
        assert "Chat2APIImageDropV1" in drop_script.text
        assert drop_script.headers["x-content-type-options"] == "nosniff"
        assert "no-store" in drop_script.headers["cache-control"]
        assert page.text.index("chat2api-image-drop-v1.js") < page.text.index("chat2api-chat-client-v1.js")
        assert "Chat2APIImageDropV1.install" in script.text
        assert "drag-over" in client.get("/assets/chat2api-chat-client-v1.css").text

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
