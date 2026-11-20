from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_commerce import USER_SESSION_COOKIE
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def test_native_renderer_rejects_active_nodes_and_untrusted_urls() -> None:
    node = shutil.which("node")
    if node is None:
        return
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [node, "--test", "tests/web_chat_native_renderer_v1.mjs"],
        cwd=root, check=False, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "native-renderer-validation-passed" in result.stdout


def test_user_native_content_adapter_enforces_key_owner_and_preserves_validated_svg(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-native-test-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    native_snapshot = {
        "version": 1,
        "revision": 4,
        "root": {
            "tag": "svg",
            "attrs": {"viewBox": "0 0 10 10", "width": "10", "height": "10"},
            "children": [{"tag": "path", "attrs": {"d": "M0 0 L10 10", "stroke": "#123456"}}],
        },
        "assets": [],
        "files": [],
        "finality": "verified-final",
    }

    async def snapshot_loader(request_id, owner, *, watch_pending_archive=False, preserve_native_tree=False):
        if request_id != "req_nativeproof1234" or not owner:
            return None
        result = {**native_snapshot}
        if watch_pending_archive:
            result["_pending_archive_pollable"] = False
        return result

    app.state.native_response_snapshot = snapshot_loader
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        first = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "native-owner@example.test", "password": "native-owner-password"},
        )
        assert first.status_code == 200, first.text
        owner_cookie = client.cookies.get(USER_SESSION_COOKIE)
        owner_key = client.post(
            "/api/user/keys",
            headers={"Origin": "http://testserver"},
            json={"name": "native owner key"},
        )
        assert owner_key.status_code == 200, owner_key.text
        owner_key_id = owner_key.json()["key"]["key_id"]

        client.cookies.clear()
        second = client.post(
            "/api/user/register",
            headers={"Origin": "http://testserver"},
            json={"email": "native-other@example.test", "password": "native-other-password"},
        )
        assert second.status_code == 200, second.text
        other_cookie = client.cookies.get(USER_SESSION_COOKIE)

        asyncio.run(app.state.telemetry.upsert({
            "request_id": "req_nativeproof1234",
            "api_key_id": owner_key_id,
            "status": "completed",
            "response_media": [],
            "response_generated_files": [],
        }))

        client.cookies.clear()
        unauthenticated = client.get("/api/user/chat/requests/req_nativeproof1234/native-content")
        assert unauthenticated.status_code == 401
        assert unauthenticated.headers["cache-control"] == "private, no-store"

        client.cookies.set(USER_SESSION_COOKIE, other_cookie)
        hidden = client.get("/api/user/chat/requests/req_nativeproof1234/native-content")
        assert hidden.status_code == 404

        client.cookies.set(USER_SESSION_COOKIE, owner_cookie)
        response = client.get("/api/user/chat/requests/req_nativeproof1234/native-content")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["finality"] == "verified-final"
        assert body["root"]["tag"] == "svg"
        assert body["root"]["attrs"]["viewBox"] == "0 0 10 10"
        assert body["root"]["children"][0]["attrs"]["d"] == "M0 0 L10 10"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["vary"] == "Cookie"

        events = client.get("/api/user/chat/requests/req_nativeproof1234/native-content/events")
        assert events.status_code == 200, events.text
        assert "event: native.snapshot" in events.text
        assert '"finality":"verified-final"' in events.text
        assert "event: native.end" in events.text
        assert events.headers["cache-control"] == "private, no-store"
        assert events.headers["vary"] == "Cookie"
