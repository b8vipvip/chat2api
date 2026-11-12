from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.user_console_v104_patch import install_user_console_v104_patch


def test_chat_client_page_assets_and_auth_boundary(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_API_KEY="web-chat-test-master-key",
        CHAT2API_DATA_DIR=str(tmp_path),
    )
    app = create_app(settings)
    install_user_console_v104_patch(app)

    with TestClient(app) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert 'id="messageInput"' in page.text
        assert "/assets/chat2api-chat-client-v1.js" in page.text
        assert "/assets/chat2api-chat-client-v1.css" in page.text
        assert page.headers["content-security-policy"].startswith("default-src 'self'")
        assert "unsafe-inline" not in page.headers["content-security-policy"]
        assert page.headers["x-content-type-options"] == "nosniff"

        script = client.get("/assets/chat2api-chat-client-v1.js")
        assert script.status_code == 200
        assert "POST" in script.text
        assert "/v1/chat/completions" in script.text
        assert "localStorage" in script.text
        assert "state.apiToken" in script.text

        stylesheet = client.get("/assets/chat2api-chat-client-v1.css")
        assert stylesheet.status_code == 200
        assert ".sidebar" in stylesheet.text
        assert "@media(max-width:760px)" in stylesheet.text

        console = client.get("/console")
        assert console.status_code == 200
        assert 'href="/chat"' in console.text

        # The HTML shell is public, but all user data and API Key operations
        # remain behind the existing user-session boundary.
        assert client.get("/api/user/me").status_code == 401
        assert client.get("/api/user/keys").status_code == 401
