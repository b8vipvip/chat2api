from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def test_chat_client_stream_terminal_handling() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Web Chat stream regression")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--test", "tests/web_chat_stream_terminal_v1.mjs"], cwd=root, check=True, timeout=20)


def test_chat_client_page_assets_and_auth_boundary(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-test-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert 'id="messageInput"' in page.text
        assert "/assets/chat2api-chat-client-v1.js" in page.text
        assert "/assets/chat2api-chat-client-v1.css" in page.text
        assert "/assets/katex/0.19.1/katex.min.js" in page.text
        assert "/assets/katex/0.19.1/katex.min.css" in page.text
        assert page.headers["content-security-policy"].startswith("default-src 'self'")
        assert "unsafe-inline" not in page.headers["content-security-policy"]
        assert page.headers["x-content-type-options"] == "nosniff"

        script = client.get("/assets/chat2api-chat-client-v1.js")
        assert script.status_code == 200
        assert "POST" in script.text
        assert "/api/user/chat/completions" in script.text
        assert "/v1/chat/completions" not in script.text
        assert "streamNativeContent" in script.text
        assert "/api/user/chat/requests/" in script.text
        assert "/api/user/chat/requests/" in script.text
        assert "localStorage" in script.text
        assert "/api/user/chat/conversations" in script.text
        assert "syncServerConversations" in script.text
        assert "normalizeToolEvents" in script.text
        assert "chat2api_references" in script.text
        assert "applyMessageReferences" in script.text
        assert "normalizeToolReferences(message.references)" in script.text
        assert "...(references.length ? {references} : {})" in script.text
        assert "const mergedReferences = normalizeToolReferences([" in script.text
        assert "A newer text snapshot must not erase server-side citations." in script.text
        assert "renderStandaloneMessageReferences" in script.text
        assert 'eventName === "chat.references"' in script.text
        assert "const toolEvents = normalizeToolEvents(message.toolEvents,message.role)" in script.text
        assert "expected_revision" in script.text
        assert "orphanedFileIds" not in script.text
        assert "remainingFileIds" not in script.text
        assert "state.apiToken" in script.text
        assert "prompt_mode" not in script.text
        assert 'await apiJson("/api/user/me")' in script.text

        stylesheet = client.get("/assets/chat2api-chat-client-v1.css")
        assert stylesheet.status_code == 200
        assert ".sidebar" in stylesheet.text
        assert "@media(max-width:760px)" in stylesheet.text

        # CI and production images vendor the exact pinned distribution. A plain
        # source checkout may omit generated assets and then keeps text fallback.
        katex_root = Path(__file__).resolve().parents[1] / "app" / "vendor" / "katex"
        if (katex_root / "katex.min.js").is_file():
            katex_script = client.get("/assets/katex/0.19.1/katex.min.js")
            assert katex_script.status_code == 200
            assert "katex" in katex_script.text.lower()
            assert "immutable" in katex_script.headers["cache-control"]
            katex_css = client.get("/assets/katex/0.19.1/katex.min.css")
            assert katex_css.status_code == 200
            assert "KaTeX_Main-Regular.woff2" in katex_css.text
            katex_font = client.get(
                "/assets/katex/0.19.1/fonts/KaTeX_Main-Regular.woff2"
            )
            assert katex_font.status_code == 200
            assert katex_font.headers["content-type"].startswith("font/woff2")
            assert "immutable" in katex_font.headers["cache-control"]
            assert client.get(
                "/assets/katex/0.19.1/fonts/../../main.py"
            ).status_code == 404

        console = client.get("/console")
        assert console.status_code == 200
        assert 'href="/chat"' in console.text

        # The HTML shell is public, but all user data and API Key operations
        # remain behind the existing user-session boundary.
        assert client.get("/api/user/me").status_code == 401
        assert client.get("/api/user/keys").status_code == 401
