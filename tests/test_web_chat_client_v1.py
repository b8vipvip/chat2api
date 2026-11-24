import asyncio
import base64
from pathlib import Path
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.control_plane_v1_patch import install_control_plane_v1
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
    install_control_plane_v1(app)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    with TestClient(app) as client:
        page = client.get("/chat")
        assert page.status_code == 200
        assert 'id="messageInput"' in page.text
        assert "会话会同步保存在你的 Chat2API 账户中" in page.text
        assert "当前浏览器仅缓存每条消息前 8,000 个字符" in page.text
        assert "聊天记录保存在当前浏览器" not in page.text
        assert 'id="exportJsonButton"' in page.text
        assert 'id="importButton"' in page.text
        assert 'id="conversationImportInput"' in page.text
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
        assert "MAX_USER_MESSAGE_CHARS = 8000" in script.text
        assert "MAX_ASSISTANT_MESSAGE_CHARS = 32000" in script.text
        assert "MAX_LOCAL_MESSAGE_CHARS = 8000" in script.text
        assert "MAX_MESSAGES = 100" in script.text
        assert "MAX_VOICE_CONTEXT_CHARS = 8000" in script.text
        assert "buildVoiceContext(voiceConversation)" in script.text
        assert "instructions:DEFAULT_VOICE_INSTRUCTIONS" in script.text
        assert "已将当前对话背景作为开场消息发送" in script.text
        assert "payload.context_injected === true" in script.text
        assert "payload.instructions_applied === true" in script.text
        assert "已发送语音开场指令" in script.text
        assert "MAX_LOCAL_MESSAGES = 30" in script.text
        assert "normalizeConversationRow(row, MAX_LOCAL_MESSAGES)" in script.text
        assert "slice(-MAX_LOCAL_MESSAGES)" in script.text
        assert "slice(-MAX_MESSAGES)" in script.text
        assert "ASSISTANT_TRUNCATION_NOTICE" in script.text
        assert "limitAssistantText" in script.text
        assert "normalizeMessageContent(message.content, message.role, MAX_LOCAL_MESSAGE_CHARS)" in script.text
        assert "5 * 1024 * 1024" in script.text
        assert "shouldAutoScroll(screen)" in script.text
        assert "state.forceScrollToBottom" in script.text
        assert "else screen.scrollTop = previousScrollTop" in script.text
        assert "/api/user/chat/requests/" in script.text
        assert "/api/user/chat/requests/" in script.text
        assert "localStorage" in script.text
        assert "/api/user/chat/conversations" in script.text
        assert "/api/user/chat/conversations/search" in script.text
        assert "/api/user/chat/conversations/import" in script.text
        assert "exportConversationJson" in script.text
        assert "importConversationJson" in script.text
        assert "syncServerConversations" in script.text
        assert "normalizeToolEvents" in script.text
        assert "chat2api_references" in script.text
        assert "applyMessageReferences" in script.text
        assert "normalizeToolReferences(message.references)" in script.text
        assert "...(references.length ? {references} : {})" in script.text
        assert "const mergedReferences = normalizeToolReferences([" in script.text
        assert "A newer text snapshot must not erase server-side citations." in script.text
        assert "renderStandaloneMessageReferences" in script.text
        assert "citationTextForMessage" in script.text
        assert "引用来源" in script.text
        assert 'eventName === "chat.references"' in script.text
        assert "const toolEvents = normalizeToolEvents(message.toolEvents,message.role)" in script.text
        assert "expected_revision" in script.text
        assert "orphanedFileIds" not in script.text
        assert "remainingFileIds" not in script.text
        assert "state.apiToken" not in script.text
        assert "apiKeySelect" not in script.text
        assert "/api/user/keys" not in script.text
        assert "Authorization" not in script.text
        assert "/api/user/chat/session" in script.text
        assert "/api/user/chat/models" in script.text
        assert "/api/user/chat/files" in script.text
        assert "/api/user/chat/voice-session" in script.text
        assert "X-Chat2API-User-Key-ID" in script.text
        assert "prompt_mode" not in script.text
        assert 'await apiJson("/api/user/me")' in script.text
        voice_content = (Path(__file__).resolve().parents[1] / "chrome_extension" / "content_voice_live.js").read_text(encoding="utf-8")
        voice_text = (Path(__file__).resolve().parents[1] / "chrome_extension" / "content_voice_live_text_v24.js").read_text(encoding="utf-8")
        live_start = voice_content[voice_content.index("  async function startLive(message) {"):voice_content.index("  async function stopLive(")]
        assert "textBridge.prepareContext(active,primer)" in live_start
        assert live_start.index("textBridge.prepareContext(active,primer)") < live_start.index("await openVoice(active)")
        assert live_start.index("await openVoice(active)") < live_start.index("startTranscriptPolling(active)")
        assert "async function prepareContext(active, text)" in voice_text
        assert "function messageIdentity(node)" in voice_text
        assert "waitForAssistantCompletion(active,baselineIdentity,baselineText)" in voice_text
        assert "active.instructionsApplied = true" in live_start
        assert "active.contextInjected = Boolean(active.context.trim())" in live_start
        assert '"instructions_only_setup_turn"' in live_start

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


def test_web_chat_uses_shared_server_key_but_keeps_user_identity_isolated(tmp_path: Path) -> None:
    settings = Settings(CHAT2API_DATA_DIR=str(tmp_path))
    app = create_app(settings)
    install_control_plane_v1(app)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)
    shared_public, shared_token = asyncio.run(app.state.api_keys.create("web-chat-shared"))

    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}

    with TestClient(app) as client:
        for email in ("webchat-a@example.com", "webchat-b@example.com"):
            registered = client.post("/api/user/register", json={
                "email": email, "password": "web-chat-password-123",
            })
            assert registered.status_code == 200, registered.text
        login_a = client.post("/api/user/login", json={
            "email": "webchat-a@example.com", "password": "web-chat-password-123",
        })
        assert login_a.status_code == 200, login_a.text
        # A valid account session is not enough until the administrator configures
        # the shared service key.
        assert client.get("/api/user/chat/session").status_code == 503

        app.state.control_plane.update_client({"api_key_id": shared_public["key_id"]})
        session_a = client.get("/api/user/chat/session")
        assert session_a.status_code == 200, session_a.text
        key_a = session_a.json()["key_id"]
        assert key_a and key_a != shared_public["key_id"]
        assert shared_token not in session_a.text
        assert shared_public["key_id"] not in session_a.text
        assert client.get("/api/client/config").json()["api_key_configured"] is True
        assert "api_key_id" not in client.get("/api/client/config").json()
        assert client.get("/api/user/keys").json()["data"] == []

        upload_a = client.post("/api/user/chat/files", headers={
            **origin, "X-Chat2API-User-Key-ID": key_a,
        }, json={
            "filename": "note.txt",
            "mime_type": "text/plain",
            "data_base64": base64.b64encode(b"private A").decode("ascii"),
            "purpose": "assistants",
        })
        assert upload_a.status_code == 200, upload_a.text
        file_a = upload_a.json()["id"]

        login_b = client.post("/api/user/login", json={
            "email": "webchat-b@example.com", "password": "web-chat-password-123",
        })
        assert login_b.status_code == 200, login_b.text
        session_b = client.get("/api/user/chat/session")
        assert session_b.status_code == 200, session_b.text
        key_b = session_b.json()["key_id"]
        assert key_b and key_b != key_a

        cross_user_upload = client.post("/api/user/chat/files", headers={
            **origin, "X-Chat2API-User-Key-ID": key_a,
        }, json={
            "filename": "note.txt",
            "mime_type": "text/plain",
            "data_base64": base64.b64encode(b"private B").decode("ascii"),
            "purpose": "assistants",
        })
        assert cross_user_upload.status_code == 404

        upload_b = client.post("/api/user/chat/files", headers={
            **origin, "X-Chat2API-User-Key-ID": key_b,
        }, json={
            "filename": "note.txt",
            "mime_type": "text/plain",
            "data_base64": base64.b64encode(b"private B").decode("ascii"),
            "purpose": "assistants",
        })
        assert upload_b.status_code == 200, upload_b.text
        assert app.state.file_store.get(file_a).owner_key_id == key_a
        assert app.state.file_store.get(upload_b.json()["id"]).owner_key_id == key_b


def test_web_chat_usage_is_counted_for_user_and_shared_key_once(tmp_path: Path) -> None:
    app = create_app(Settings(CHAT2API_DATA_DIR=str(tmp_path)))
    api_keys = app.state.api_keys

    async def exercise():
        shared_public, _shared_token = await api_keys.create("shared-web-chat")
        user_identity = await api_keys.ensure_web_chat_user_key("user_web_chat_usage")
        record = {
            "request_id": "req_web_chat_shared_usage_0001",
            "api_key_id": user_identity.key_id,
            "shared_api_key_id": shared_public["key_id"],
            "status": "completed",
            "usage": {"total_tokens": 73},
        }
        await api_keys.account_completed_usage(record)
        await api_keys.account_completed_usage(record)
        return shared_public["key_id"], user_identity.key_id

    shared_key_id, user_key_id = asyncio.run(exercise())
    assert api_keys.keys[shared_key_id].tokens_used == 73
    assert api_keys.keys[user_key_id].tokens_used == 73
    assert api_keys.get_public(user_key_id) is None
    assert all(row["key_id"] != user_key_id for row in api_keys.list_public())
