import asyncio
import time
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
from app.v17_patch import install_v17_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def test_chat_client_stream_terminal_handling() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the Web Chat stream regression")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--test", "tests/web_chat_stream_terminal_v1.mjs"], cwd=root, check=True, timeout=20)


def test_chat_client_autonomous_plugin_tool_loop() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required for the autonomous plugin tool-loop regression")
    root = Path(__file__).resolve().parents[1]
    subprocess.run([node, "--test", "tests/web_chat_plugin_tool_loop_v1.mjs"], cwd=root, check=True, timeout=20)


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
        assert "会话同步保存在当前账户" in page.text
        assert "客户端诊断日志不会记录你的消息或模型回复内容" in page.text
        assert "ChatGPT" in page.text
        assert 'id="webChatLoginDialog"' in page.text
        assert 'id="guestLoginButton"' in page.text
        assert 'id="webChatOpenLoginButton"' in page.text
        assert 'href="/console"' not in page.text
        assert 'id="pluginsButton"' in page.text
        assert 'id="libraryButton"' in page.text
        assert 'id="sidebarAccount"' in page.text
        assert 'id="accountSettingsButton" href="/settings">设置</a>' in page.text
        topbar_html = page.text.split('<header class="topbar"', 1)[1].split("</header>", 1)[0]
        composer_html = page.text.split('<footer class="composer-area"', 1)[1].split("</footer>", 1)[0]
        assert 'id="modelSelect"' not in topbar_html
        assert 'id="modelSelect"' in composer_html
        assert 'id="pluginPickerButton"' in composer_html
        assert 'id="pluginsBackToChat"' in page.text
        sidebar_html = page.text.split('<aside class="sidebar"', 1)[1].split("</aside>", 1)[0]
        assert 'id="newChatButton"' not in sidebar_html
        assert 'id="pluginsButton"' in sidebar_html and 'id="libraryButton"' in sidebar_html
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
        assert "当前对话背景已作为开场消息发送" in script.text
        assert "payload.context_injected === true" in script.text
        assert "payload.instructions_applied === true" in script.text
        assert "语音开场指令已发送" in script.text
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
        assert "/api/user/chat/client-logs" in script.text
        assert "/api/user/guest-session" in script.text
        assert "登录成功后会留在当前聊天页面" in page.text
        assert "chat.request.started" in script.text
        assert "webchat.models.catalog_loaded" in script.text
        assert "attachment.image.upload_completed" in script.text
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
        assert "/api/user/plugins" in script.text
        assert "/api/user/plugins/oauth/" in script.text
        assert "selectedPlugins" in script.text
        assert "restorePluginSelection" in script.text
        assert "selectedPluginToolDefinitions" in script.text
        assert "buildPluginPlannerPrompt" in script.text
        assert "parsePluginToolPlan" in script.text
        assert "validatePluginToolArguments" in script.text
        assert "executePluginToolCall" in script.text
        assert "runAutonomousPluginToolLoop" in script.text
        assert 'assistant.requestId = "";' in script.text
        assert 'message.recoveryStatus === "tool_planning"' in script.text
        assert "页面刷新导致工具调用中断" in script.text
        assert "maxRounds = 3" in script.text
        assert "maxToolCalls = 6" in script.text
        assert "knownGmailMessageIds" in script.text
        assert 'role:"tool"' in script.text
        assert "stream:false" in script.text
        assert "tool_calls" in script.text
        assert "shouldQueryPlugin" not in script.text
        assert "runSelectedPluginsForPrompt" not in script.text
        assert "buildPluginContextMessage" not in script.text
        assert "list_issues" in script.text
        assert "不可信数据" in script.text
        assert '$("sidebarAccount").hidden = false;' in script.text
        assert '$("accountSettingsButton").hidden = false;' in script.text
        assert "mapped_model_id" in script.text
        assert 'apiJson("/api/user/models")' not in script.text
        assert "当前账户或服务端统一 API Key 没有可用模型" in script.text
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
        assert ".message.assistant .message-body" in stylesheet.text
        assert ".message.user .message-body" in stylesheet.text
        assert ".conversation-list{gap:2px;padding:0 2px}" in stylesheet.text
        assert ".topbar-actions,.connection-state{display:none!important}" in stylesheet.text

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

        console = client.get("/console", follow_redirects=False)
        assert console.status_code == 303
        assert console.headers["location"] == "/settings"
        settings_page = client.get("/settings")
        assert settings_page.status_code == 200
        assert "账户设置" in settings_page.text
        assert "控制台" not in settings_page.text

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
        # The internal ownership identity is never exposed as a user API key
        # and cannot be revealed, quota-edited or revoked through legacy routes.
        assert client.get("/api/user/keys").json()["data"] == []
        assert client.get(f"/api/user/keys/{key_a}/secret").status_code == 409
        assert client.patch(
            f"/api/user/keys/{key_a}/quota", json={"token_quota": 100},
        ).status_code == 409
        assert client.delete(f"/api/user/keys/{key_a}").status_code == 409
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


def test_configured_client_key_is_mode1_only_and_user_created_keys_remain_mode2(tmp_path: Path) -> None:
    settings = Settings(
        CHAT2API_DATA_DIR=str(tmp_path),
        CHAT2API_ADMIN_USERNAME="owner",
        CHAT2API_ADMIN_PASSWORD="strong-root-password",
    )
    app = create_app(settings)
    install_control_plane_v1(app)
    install_v17_patch(app)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)
    origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}

    with TestClient(app) as client:
        registered = client.post("/api/user/register", json={
            "email": "mode-routing@example.com",
            "password": "mode-routing-user-password-123",
        })
        assert registered.status_code == 200, registered.text
        logged_in = client.post("/api/user/login", json={
            "email": "mode-routing@example.com",
            "password": "mode-routing-user-password-123",
        })
        assert logged_in.status_code == 200, logged_in.text

        created_user_key = client.post("/api/user/keys", json={"name": "user-mode2-key"})
        assert created_user_key.status_code == 200, created_user_key.text
        user_key_id = created_user_key.json()["key"]["key_id"]
        user_token = created_user_key.json()["token"]
        assert app.state.user_accounts.owner_for_key(user_key_id)

        # A user-created key cannot be promoted into the server's shared Mode 1
        # credential, even by a root administrator.
        admin_login = client.post(
            "/api/admin/auth/login",
            headers=origin,
            json={"username": "owner", "password": "strong-root-password"},
        )
        assert admin_login.status_code == 200, admin_login.text
        rejected_user_key = client.put(
            "/api/admin/control/client",
            headers=origin,
            json={"api_key": user_token},
        )
        assert rejected_user_key.status_code == 400
        assert "Mode 2" in rejected_user_key.json()["detail"]
        assert app.state.control_plane.client_api_key_id() == ""

        shared_public, shared_token = asyncio.run(
            app.state.api_keys.create("server-managed-web-chat")
        )
        configured = client.put(
            "/api/admin/control/client",
            headers=origin,
            json={"api_key": shared_token},
        )
        assert configured.status_code == 200, configured.text
        assert app.state.control_plane.client_api_key_id() == shared_public["key_id"]

        # The configured server key is reserved for Mode 1 and is rejected by
        # every endpoint protected by the generic Mode 2 API-key dependency.
        shared_on_mode2 = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {shared_token}"},
        )
        assert shared_on_mode2.status_code == 403
        assert "Mode 1" in shared_on_mode2.json()["error"]["message"]

        # User-created keys remain ordinary OpenAI-compatible Mode 2 keys.
        user_on_mode2 = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {user_token}"},
        )
        assert user_on_mode2.status_code == 200, user_on_mode2.text

        # The browser uses its HttpOnly account session; neither the shared
        # service key nor the user's API token is returned by the session API.
        web_chat_session = client.get("/api/user/chat/session")
        assert web_chat_session.status_code == 200, web_chat_session.text
        assert web_chat_session.json()["key_id"] not in {user_key_id, shared_public["key_id"]}
        assert shared_token not in web_chat_session.text
        assert user_token not in web_chat_session.text

        # Removing the Web Chat configuration releases the server key back to
        # its ordinary Mode 2 behavior; the user's own key is unaffected.
        cleared = client.put(
            "/api/admin/control/client", headers=origin, json={"clear_api_key": True},
        )
        assert cleared.status_code == 200, cleared.text
        shared_after_clear = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {shared_token}"},
        )
        assert shared_after_clear.status_code == 200, shared_after_clear.text
        user_after_clear = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {user_token}"},
        )
        assert user_after_clear.status_code == 200, user_after_clear.text


def test_web_chat_usage_is_counted_for_user_and_shared_key_once(tmp_path: Path) -> None:
    app = create_app(Settings(CHAT2API_DATA_DIR=str(tmp_path)))
    api_keys = app.state.api_keys

    async def exercise():
        shared_public, _shared_token = await api_keys.create("shared-web-chat")
        user_identity = await api_keys.ensure_web_chat_user_key("user_web_chat_usage")
        user_key_public, _user_key_token = await api_keys.create("user-managed-api-key")
        record = {
            "request_id": "req_web_chat_shared_usage_0001",
            "api_key_id": user_identity.key_id,
            "shared_api_key_id": shared_public["key_id"],
            "web_chat_mode": True,
            "auth_kind": "web_chat",
            "status": "completed",
            "usage": {"total_tokens": 73},
        }
        await api_keys.account_completed_usage(record)
        await api_keys.account_completed_usage(record)
        regular_record = {
            "request_id": "req_regular_api_usage_0002",
            "api_key_id": user_key_public["key_id"],
            "web_chat_mode": False,
            "status": "completed",
            "usage": {"total_tokens": 19},
        }
        await api_keys.account_completed_usage(regular_record)
        return shared_public["key_id"], user_identity.key_id, user_key_public["key_id"]

    shared_key_id, identity_key_id, user_key_id = asyncio.run(exercise())
    assert api_keys.keys[shared_key_id].tokens_used == 73
    # Web Chat must not consume a user's legacy API-key quota; normal API calls
    # still account against their caller key.
    assert api_keys.keys[identity_key_id].tokens_used == 0
    assert api_keys.keys[user_key_id].tokens_used == 19
    assert api_keys.get_public(identity_key_id) is None
    assert all(row["key_id"] != identity_key_id for row in api_keys.list_public())


def test_guest_web_chat_is_policy_gated_and_uses_account_scoped_identity(tmp_path: Path) -> None:
    settings = Settings(CHAT2API_DATA_DIR=str(tmp_path))
    app = create_app(settings)
    install_control_plane_v1(app)
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)
    shared_public, _shared_token = asyncio.run(app.state.api_keys.create("guest-web-chat-shared"))

    with TestClient(app) as client:
        origin = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
        assert client.post("/api/user/guest-session", headers=origin).status_code == 403
        app.state.control_plane.update_client({"api_key_id": shared_public["key_id"], "guest_enabled": True})

        created = client.post("/api/user/guest-session", headers=origin)
        assert created.status_code == 200, created.text
        guest = created.json()["user"]
        assert guest["is_guest"] is True
        assert guest["display_name"] == "游客"
        assert "password_hash" not in created.text
        assert client.get("/api/user/me").json()["user"]["is_guest"] is True
        assert client.get("/api/user/keys").status_code == 403
        guest_settings = client.get("/settings", follow_redirects=False)
        assert guest_settings.status_code == 303
        assert guest_settings.headers["location"] == "/chat"
        legacy_guest_settings = client.get("/console", follow_redirects=False)
        assert legacy_guest_settings.status_code == 303
        assert legacy_guest_settings.headers["location"] == "/chat"

        session = client.get("/api/user/chat/session")
        assert session.status_code == 200, session.text
        identity_key_id = session.json()["key_id"]
        assert identity_key_id
        assert identity_key_id != shared_public["key_id"]

        # Expired guest account rows and their internal ownership keys are
        # removed before the next anonymous session is created.
        old_user_id = guest["user_id"]
        accounts = app.state.user_accounts
        with accounts.lock:
            accounts.users[old_user_id]["guest_expires_at"] = time.time() - 1
            for session_row in accounts.sessions.values():
                if session_row.get("user_id") == old_user_id:
                    session_row["expires_at"] = time.time() - 1
            accounts._save()
        renewed = client.post("/api/user/guest-session", headers=origin)
        assert renewed.status_code == 200, renewed.text
        assert renewed.json()["user"]["user_id"] != old_user_id
        assert old_user_id not in accounts.users
        assert identity_key_id not in app.state.api_keys.keys

        app.state.control_plane.update_client({"guest_enabled": False})
        blocked = client.get("/api/user/chat/session")
        assert blocked.status_code == 403
        assert "游客使用已关闭" in blocked.text


def test_every_web_chat_session_expiry_path_opens_inline_login_dialog() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "chat_client_v1.js").read_text(encoding="utf-8")
    assert 'if (response.status === 401) {\n          state.user = null;\n          $("sidebarAccount").hidden = true;\n          showOnly("authRequired");\n          setConnection("登录已失效", "error");\n          openLoginDialog();' in source
    assert 'state.user = null;\n        $("sidebarAccount").hidden = true;\n        showOnly("authRequired");\n        setConnection("登录已失效", "error");\n        openLoginDialog();\n        throw new Error("用户登录状态已失效，请重新登录后再继续聊天")' in source
    assert 'state.user = null;\n        $("sidebarAccount").hidden = true;\n        showOnly("authRequired");\n        openLoginDialog();\n        $("composerArea").classList.add("hidden");' in source


def test_guest_policy_revocation_also_opens_inline_login_dialog() -> None:
    source = (Path(__file__).resolve().parents[1] / "app" / "chat_client_v1.js").read_text(encoding="utf-8")
    assert 'function guestLoginRequired(status, payload)' in source
    assert 'String(payload?.detail || "").includes("游客使用已关闭")' in source
    assert 'else if (guestLoginRequired(response.status, payload))' in source
    assert 'else if (guestLoginRequired(response.status, payload)) {\n        state.user = null;\n        $("sidebarAccount").hidden = true;\n        showOnly("authRequired");' in source
    assert 'const guestAccessClosed = guestLoginRequired(response.status, payload);' in source
    assert source.count('openLoginDialog(String(payload.detail || "游客使用已关闭，请登录账户"));') == 2


def test_github_plugin_ui_prompts_for_oauth_scope_reauthorization() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "chat_client_v1.js"
    ).read_text(encoding="utf-8")
    assert 'plugin.reauthorization_required === true' in source
    assert '"重新授权并更新权限"' in source
    assert 'await apiJson("/api/user/plugins/" + encodeURIComponent(plugin.id) + "/disconnect", {method:"POST"});' in source
    assert 'plugin.connected && !plugin.reauthorization_required' in source
    assert 'if (plugin.connected && plugin.enabled && plugin.configured && !needsReauthorization)' in source


def test_web_chat_plugin_and_live_video_copy_matches_supported_capabilities() -> None:
    html = (
        Path(__file__).resolve().parents[1] / "app" / "chat_client_v1.html"
    ).read_text(encoding="utf-8")
    assert "PR 详情及变更文件/差异分页读取" in html
    assert "每秒最多 5 帧连续发送" in html
    assert "不支持或轨道中断时会明确降级为快照" in html
    assert "不保证音视频帧级同步" in html
    assert "每秒最多一张快照" not in html


def test_github_scope_downgrade_ui_requires_confirmed_remote_revocation() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "app" / "chat_client_v1.js"
    ).read_text(encoding="utf-8")
    assert 'plugin.reauthorization_mode === "downgrade"' in source
    assert 'require_provider_revocation:requireRevocation' in source
    assert '["revoked","already_revoked"].includes(disconnected.provider_revocation)' in source
    assert "已保留原连接" in source
