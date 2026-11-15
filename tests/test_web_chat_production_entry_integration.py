from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_production_entry_installs_web_chat_recovery_and_download_boundaries(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[1]
    script = r"""
from app.entry import app
from fastapi.testclient import TestClient

assert app.state.web_chat_features_v1_installed is True
assert app.state.web_chat_integration_version == 1
expected = {
    ("GET", "/api/user/chat/requests/{request_id}/recovery"),
    ("POST", "/api/user/chat/requests/{request_id}/cancel"),
    ("GET", "/api/user/chat/files/{file_id}/download"),
    ("GET", "/api/user/files/{file_id}/content"),
    ("DELETE", "/api/user/files/{file_id}"),
    ("GET", "/assets/chat2api-image-drop-v1.js"),
    ("GET", "/assets/chat2api-chat-renderer-v2.js"),
    ("GET", "/assets/chat2api-chat-stream-recovery-v1.js"),
}
for method, path in expected:
    matches = [
        route for route in app.routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    ]
    assert len(matches) == 1, (method, path, len(matches))

with TestClient(app) as client:
    recovery = client.get("/api/user/chat/requests/req_12345678/recovery")
    assert recovery.status_code == 401
    assert recovery.headers["cache-control"] == "private, no-store"
    download = client.get("/api/user/chat/files/file_12345678/download")
    assert download.status_code == 401
    assert download.headers["cache-control"] == "private, no-store"
    image_content = client.get("/api/user/files/file_12345678/content")
    assert image_content.status_code == 401
    assert image_content.headers["cache-control"] == "private, no-store"
    image_delete = client.delete("/api/user/files/file_12345678", headers={"Origin": "http://testserver"})
    assert image_delete.status_code == 401
    assert image_delete.headers["cache-control"] == "private, no-store"

    # Exercise the actual app.entry composition, not a manually installed route.
    registered = client.post(
        "/api/user/register",
        headers={"Origin": "http://testserver"},
        json={"email": "entry-document@example.test", "password": "entry-document-password"},
    )
    assert registered.status_code == 200, registered.text
    key = client.post(
        "/api/user/keys",
        headers={"Origin": "http://testserver"},
        json={"name": "entry document key"},
    )
    assert key.status_code == 200, key.text
    token = key.json()["token"]
    import base64
    payload = b"%PDF-1.4\\n% app.entry download test\\n%%EOF\\n"
    uploaded = client.post(
        "/v1/files",
        headers={"Authorization": "Bearer " + token},
        json={
            "filename": "entry-proof.pdf",
            "mime_type": "application/pdf",
            "data_base64": base64.b64encode(payload).decode("ascii"),
            "purpose": "assistants",
        },
    )
    assert uploaded.status_code == 200, uploaded.text
    file_id = uploaded.json()["id"]
    downloaded = client.get(f"/api/user/chat/files/{file_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == payload
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    deleted = client.delete(
        f"/api/user/files/{file_id}", headers={"Origin": "http://testserver"},
    )
    assert deleted.status_code == 200, deleted.text
    assert client.get(f"/api/user/chat/files/{file_id}/download").status_code == 404
    assert image_delete.status_code == 401
    assert image_delete.headers["cache-control"] == "private, no-store"
    renderer = client.get("/assets/chat2api-chat-renderer-v2.js")
    assert renderer.status_code == 200
    assert "Chat2APISafeRenderer" in renderer.text
    recovery_js = client.get("/assets/chat2api-chat-stream-recovery-v1.js")
    assert recovery_js.status_code == 200
    assert "Chat2APIStreamRecoveryV1" in recovery_js.text
    image_drop = client.get("/assets/chat2api-image-drop-v1.js")
    assert image_drop.status_code == 200
    assert "Chat2APIImageDropV1" in image_drop.text
    page = client.get("/chat")
    assert page.status_code == 200
    assert "imageInput" in page.text
    assert "attachButton" in page.text
    assert page.text.index("chat2api-chat-renderer-v2.js") < page.text.index("chat2api-chat-client-v1.js")
    assert page.text.index("chat2api-chat-stream-recovery-v1.js") < page.text.index("chat2api-chat-client-v1.js")
    assert page.text.index("chat2api-image-drop-v1.js") < page.text.index("chat2api-chat-client-v1.js")
    assert page.text.index("chat2api-chat-renderer-v2.js") < page.text.index("chat2api-chat-client-v1.js")
    assert page.text.index("chat2api-chat-stream-recovery-v1.js") < page.text.index("chat2api-chat-client-v1.js")
    client_js = client.get("/assets/chat2api-chat-client-v1.js")
    assert client_js.status_code == 200
    assert "X-Chat2API-Request-ID" in client_js.text
    assert "recoverPendingMessage" in client_js.text
    assert "Chat2APISafeRenderer" in client_js.text
    assert "uploadPendingImages" in client_js.text
    assert "uploadPendingFiles" in client_js.text
    assert "purpose: \"assistants\"" in client_js.text
    assert "type: \"file\"" in client_js.text
    assert "X-Chat2API-Request-ID" in client_js.text

print("Web Chat production entry integration passed")
"""
    env = os.environ.copy()
    env["CHAT2API_DATA_DIR"] = str(tmp_path / "isolated-web-chat-data")
    env["CHAT2API_API_KEY"] = "web-chat-production-entry-test-key"
    env["CHAT2API_ADMIN_USERNAME"] = "web-chat-root"
    env["CHAT2API_ADMIN_PASSWORD"] = "web-chat-root-test-password"
    for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS", "GUNICORN_WORKERS"):
        env.pop(name, None)
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Web Chat production entry integration passed" in result.stdout
