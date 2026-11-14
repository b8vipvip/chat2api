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
    renderer = client.get("/assets/chat2api-chat-renderer-v2.js")
    assert renderer.status_code == 200
    assert "Chat2APISafeRenderer" in renderer.text
    recovery_js = client.get("/assets/chat2api-chat-stream-recovery-v1.js")
    assert recovery_js.status_code == 200
    assert "Chat2APIStreamRecoveryV1" in recovery_js.text
    page = client.get("/chat")
    assert page.status_code == 200
    assert page.text.index("chat2api-chat-renderer-v2.js") < page.text.index("chat2api-chat-client-v1.js")
    assert page.text.index("chat2api-chat-stream-recovery-v1.js") < page.text.index("chat2api-chat-client-v1.js")
    client_js = client.get("/assets/chat2api-chat-client-v1.js")
    assert client_js.status_code == 200
    assert "X-Chat2API-Request-ID" in client_js.text
    assert "recoverPendingMessage" in client_js.text
    assert "Chat2APISafeRenderer" in client_js.text

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
