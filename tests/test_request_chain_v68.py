from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
import subprocess

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE
from app.api_key_console_v68_patch import ADMIN_ASSET, PATCH_REVISION, install_api_key_console_v68_patch
from app.api_keys import ApiKeyStore


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_multimodal_v68_is_loaded_before_legacy_v4_and_owns_same_runtime_key():
    manifest = read("chrome_extension/manifest.json")
    source = read("chrome_extension/content_multimodal_v68.js")
    assert manifest.index('"content_multimodal_v68.js"') < manifest.index('"content_multimodal_v4.js"')
    assert 'const KEY = "__CHAT2API_MULTIMODAL_V4__"' in source
    assert 'const CONTROLLER = "multimodal-v4-r68"' in source
    assert "const REVISION = 68" in source
    assert 'strategy: "file-input"' in source
    assert 'strategy: "composer-paste"' in source
    assert 'strategy: "composer-drop"' in source
    assert "fresh-after-upload-menu" in source
    assert "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, \"files\")" in source
    assert 'attachment_strategy_fallback_v68: true' in source
    # Production's failure had no chip, preview, input consumption or mutations.
    # A zero-signal direct file-input attempt must therefore fall through to a
    # genuinely distinct composer injection strategy instead of waiting 45s again.
    assert "zeroSignal" in source
    assert "uploadBusy()" in source
    assert "dispatchPaste(file)" in source
    assert "dispatchDrop(file)" in source


def test_routed_dispatch_failure_releases_conversation_worker_reservation():
    source = read("chrome_extension/conversation_dispatch.js")
    assert "function releaseConversationReservation(requestId)" in source
    assert "router?.activeRequests" in source
    assert "router?.routes" in source
    assert "route.inflight_request_id = null" in source
    assert "activeRequests.delete(id)" in source
    publish = source.index("async function publishRoutedDispatchFailure")
    release = source.index("releaseConversationReservation(requestId)", publish)
    socket = source.index("trySendSocket(event)", publish)
    assert publish < release < socket
    assert "routed_dispatch_reservation_release_v68: true" in source
    assert "route_reservation_released: routeReservationReleased" in source


def build_api_key_app(tmp_path: Path) -> tuple[FastAPI, ApiKeyStore]:
    app = FastAPI()
    store = ApiKeyStore(tmp_path, "test-data-secret")
    app.state.api_keys = store
    app.state.settings = SimpleNamespace(api_key="master-secret")

    class Sessions:
        def authenticate(self, token):
            return token == "session-ok"

    app.state.admin_sessions = Sessions()

    @app.get("/admin")
    async def admin():
        return HTMLResponse("<html><body>console</body></html>")

    install_api_key_console_v68_patch(app)
    return app, store


def test_api_key_settings_endpoint_edits_name_and_scopes(tmp_path: Path):
    app, store = build_api_key_app(tmp_path)
    created, _token = asyncio.run(store.create("Old name"))
    key_id = created["key_id"]
    with TestClient(app) as client:
        client.cookies.set(SESSION_COOKIE, "session-ok")
        response = client.patch(
            f"/api/admin/keys/{key_id}/settings",
            json={"name": "  新令牌名称  ", "scopes": ["chat", "files"]},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["revision"] == PATCH_REVISION == 68
        assert payload["data"]["name"] == "新令牌名称"
        assert payload["data"]["scopes"] == ["chat", "files"]
        assert store.keys[key_id].scopes == ["chat", "files"]

        empty = client.patch(f"/api/admin/keys/{key_id}/settings", json={"scopes": []})
        assert empty.status_code == 422


def test_api_key_editor_asset_is_injected_and_uses_chinese_permissions(tmp_path: Path):
    app, _store = build_api_key_app(tmp_path)
    with TestClient(app) as client:
        html = client.get("/admin").text
        assert f'<script src="{ADMIN_ASSET}"></script>' in html
        script = client.get(ADMIN_ASSET).text

    for label in ("管理员", "对话", "模型列表", "文件", "图片生成", "音频/语音"):
        assert label in script
    assert 'data-api-key-edit="${action}"' in script
    assert 'iconButton("name", row.key_id, "修改令牌名称")' in script
    assert 'iconButton("scopes", row.key_id, "编辑权限")' in script
    assert "修改令牌名称" in script
    assert "编辑权限" in script
    assert 'usable ? "可用" : "停用"' in script
    assert "至少保留一个权限" in script
    assert "MutationObserver" not in script
    assert "setInterval(" not in script


def test_v68_javascript_syntax():
    for path in (
        "chrome_extension/content_multimodal_v68.js",
        "chrome_extension/conversation_dispatch.js",
        "app/admin_api_key_editor_v68.js",
    ):
        result = subprocess.run(["node", "--check", str(ROOT / path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_entry_installs_api_key_console_after_worker_presentation():
    entry = read("app/entry.py")
    assert "install_api_key_console_v68_patch(app)" in entry
    assert entry.index("install_worker_presentation_v64_patch(app)") < entry.index("install_api_key_console_v68_patch(app)")
