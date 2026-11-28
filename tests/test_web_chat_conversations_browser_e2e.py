from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn
from playwright.sync_api import sync_playwright

from app.config import Settings
from app.main import create_app
from app.control_plane_v1_patch import install_control_plane_v1
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_web_chat_conversations_sync_between_two_browser_profiles(tmp_path: Path) -> None:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    app = create_app(Settings(
        CHAT2API_DATA_DIR=tmp_path / "conversation-browser-e2e-data",
        CHAT2API_API_KEY="conversation-browser-e2e-master-key",
        CHAT2API_ADMIN_USERNAME="conversation-browser-e2e-admin",
        CHAT2API_ADMIN_PASSWORD="conversation-browser-e2e-admin-password",
        CHAT2API_PAIRING_CODE="conversation-browser-e2e-pairing-code",
        CHAT2API_PUBLIC_URL=base_url,
    ))
    install_control_plane_v1(app)
    shared_key, _shared_token = asyncio.run(app.state.api_keys.create("Web Chat conversation E2E shared"))
    app.state.control_plane.update_client({"api_key_id": shared_key["key_id"]})
    install_user_console_v104_patch(app)
    install_web_chat_features_v1(app)

    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="error", lifespan="on",
    ))
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    try:
        ready = False
        for _ in range(120):
            if server.started:
                try:
                    with urlopen(base_url + "/chat", timeout=1) as response:
                        if response.status == 200:
                            ready = True
                            break
                except (OSError, URLError):
                    pass
            time.sleep(0.25)
        assert ready, "Web Chat conversation test server did not become ready"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            device_a = browser.new_context()
            device_b = browser.new_context()
            try:
                email = "conversation-sync-browser-e2e@example.org"
                password = "conversation-sync-browser-e2e-password"
                registered = device_a.request.post(
                    base_url + "/api/user/register",
                    headers={"Origin": base_url},
                    data={"email": email, "password": password},
                )
                assert registered.status == 200, registered.text()
                # No per-user API Key is created; both devices use the account
                # session and the server-configured shared Web Chat key.
                logged_in = device_b.request.post(
                    base_url + "/api/user/login",
                    headers={"Origin": base_url},
                    data={"email": email, "password": password},
                )
                assert logged_in.status == 200, logged_in.text()

                page_a = device_a.new_page()
                page_b = device_b.new_page()
                page_a.goto(base_url + "/chat", wait_until="domcontentloaded")
                page_b.goto(base_url + "/chat", wait_until="domcontentloaded")
                for page in (page_a, page_b):
                    page.locator("#conversationList .muted-empty").wait_for(
                        state="visible", timeout=15000,
                    )
                assert device_a.request.get(base_url + "/api/user/keys").json()["data"] == []
                assert device_b.request.get(base_url + "/api/user/keys").json()["data"] == []

                conversation_id = "conv_browser_sync_001"
                created_at = "2026-11-20T00:00:00Z"
                conversation_payload = {
                    "id": conversation_id,
                    "title": "Created on device A",
                    "model": "gpt-5.6-sol",
                    "apiKeyId": "",
                    "createdAt": created_at,
                    "updatedAt": created_at,
                    "messages": [
                        {
                            "id": "message_browser_sync_01",
                            "role": "user",
                            "content": "Cross-device sync message",
                            "createdAt": created_at,
                            "requestId": "",
                            "pending": False,
                            "recoveryStatus": "",
                        },
                        {
                            "id": "message_browser_tool_01",
                            "role": "assistant",
                            "content": "Sources: \uE200cite\uE202turn0search1\uE201 and \uE200cite\uE202turn0search0\uE201",
                            "createdAt": created_at,
                            "requestId": "",
                            "pending": False,
                            "recoveryStatus": "completed",
                            "references": [{
                                "id": "turn0search1",
                                "url": "https://docs.python.org/3/library/asyncio.html",
                                "title": "Python asyncio documentation",
                                "snippet": "Async I/O library documentation",
                            }],
                            "toolEvents": [{
                                "toolName": "browser.search",
                                "callId": "call-browser-search-01",
                                "resultId": "result-browser-search-01",
                                "status": "completed",
                                "arguments": '{"query":"cross-device sync"}',
                                "output": "Found two search results.",
                                "references": [
                                    {
                                        "id": "turn0search0",
                                        "url": "https://docs.python.org/3/library/",
                                        "title": "Python library reference",
                                        "snippet": "Official language documentation",
                                    },
                                    {"url": "javascript:" + "alert(1)", "title": "Unsafe reference"},
                                ],
                            }],
                        },
                    ],
                }
                saved = page_a.evaluate(
                    """async ({conversationId, conversation}) => {
                      const response = await fetch(
                        "/api/user/chat/conversations/" + conversationId,
                        {
                          method: "PUT",
                          credentials: "same-origin",
                          headers: {"content-type": "application/json"},
                          body: JSON.stringify({expected_revision: 0, conversation})
                        }
                      );
                      return {status: response.status, body: await response.json()};
                    }""",
                    {"conversationId": conversation_id, "conversation": conversation_payload},
                )
                assert saved["status"] == 200, saved
                assert saved["body"]["conversation"]["revision"] == 1

                page_b.reload(wait_until="domcontentloaded")
                synced_title = page_b.locator(".conversation-title").filter(
                    has_text="Created on device A"
                )
                synced_title.wait_for(state="visible", timeout=15000)
                synced_title.click()
                page_b.get_by_text("Cross-device sync message").wait_for(
                    state="visible", timeout=10000,
                )
                tool_summary = page_b.get_by_text("browser.search · 已完成")
                tool_summary.wait_for(state="visible", timeout=10000)
                # Citation-bearing tool details are intentionally expanded on
                # first render; clicking the summary here would collapse them.
                assert page_b.locator(".message-tool-event").first.evaluate(
                    "(node) => node.open"
                )
                page_b.get_by_text("Found two search results.").wait_for(
                    state="visible", timeout=10000,
                )
                citation_links = page_b.locator(".message-tool-reference")
                # One source comes from the final assistant annotation and one
                # from the completed browser.search tool result. Both must be
                # shown once, with the same numbering used by inline markers.
                assert citation_links.count() == 2
                assert citation_links.nth(0).get_attribute("href") == "https://docs.python.org/3/library/"
                assert citation_links.nth(0).inner_text().startswith("[2] Python library reference")
                assert citation_links.nth(1).get_attribute("href") == "https://docs.python.org/3/library/asyncio.html"
                assert citation_links.nth(1).inner_text().startswith("[1] Python asyncio documentation")
                inline_citations = page_b.locator(".inline-citation")
                assert inline_citations.count() == 2
                assert inline_citations.nth(0).get_attribute("href") == "https://docs.python.org/3/library/asyncio.html"
                assert inline_citations.nth(0).inner_text() == "[1]"
                assert inline_citations.nth(1).get_attribute("href") == "https://docs.python.org/3/library/"
                assert inline_citations.nth(1).inner_text() == "[2]"

                # Both browser profiles write against revision 1. Exactly one
                # optimistic update may win; the other must receive a conflict.
                update_a = {**conversation_payload, "title": "Concurrent update from device A"}
                update_b = {**conversation_payload, "title": "Concurrent update from device B"}
                start_update = """({conversationId, conversation}) => {
                  window.__pendingConversationUpdate = fetch(
                    "/api/user/chat/conversations/" + conversationId,
                    {
                      method: "PUT",
                      credentials: "same-origin",
                      headers: {"content-type": "application/json"},
                      body: JSON.stringify({expected_revision: 1, conversation})
                    }
                  ).then(async response => ({status: response.status, body: await response.json()}));
                  return true;
                }"""
                page_a.evaluate(start_update, {"conversationId": conversation_id, "conversation": update_a})
                page_b.evaluate(start_update, {"conversationId": conversation_id, "conversation": update_b})
                concurrent_a = page_a.evaluate("() => window.__pendingConversationUpdate")
                concurrent_b = page_b.evaluate("() => window.__pendingConversationUpdate")
                assert sorted([concurrent_a["status"], concurrent_b["status"]]) == [200, 409]
                conflict = concurrent_a if concurrent_a["status"] == 409 else concurrent_b
                assert conflict["body"]["conversation"]["revision"] == 2
                saved_update = concurrent_a if concurrent_a["status"] == 200 else concurrent_b
                assert saved_update["body"]["conversation"]["revision"] == 2

                latest_revision = page_a.evaluate(
                    """async (conversationId) => {
                      const response = await fetch("/api/user/chat/conversations", {
                        credentials: "same-origin"
                      });
                      if (!response.ok) throw new Error("conversation list request failed");
                      const body = await response.json();
                      const conversation = body.data.find(item => item.id === conversationId);
                      return conversation?.revision ?? null;
                    }""",
                    conversation_id,
                )
                assert latest_revision is not None
                assert latest_revision >= saved_update["body"]["conversation"]["revision"]

                deleted = page_a.evaluate(
                    """async ({conversationId, expectedRevision}) => {
                      const response = await fetch(
                        "/api/user/chat/conversations/" + conversationId +
                          "?expected_revision=" + encodeURIComponent(expectedRevision),
                        {method: "DELETE", credentials: "same-origin"}
                      );
                      return {status: response.status, body: await response.json()};
                    }""",
                    {
                        "conversationId": conversation_id,
                        "expectedRevision": latest_revision,
                    },
                )
                assert deleted["status"] == 200, deleted

                # Device B still has the old row in its local cache. The
                # server tombstone must win on refresh and prevent resurrection.
                page_b.reload(wait_until="domcontentloaded")
                page_b.wait_for_function(
                    "() => !Array.from(document.querySelectorAll('.conversation-title'))"
                    ".some(node => node.textContent === 'Created on device A')",
                    timeout=15000,
                )
                assert page_b.locator(".muted-empty").inner_text() == "还没有对话"
            finally:
                device_a.close()
                device_b.close()
                browser.close()
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)


def test_web_chat_native_snapshot_adapts_to_client_theme() -> None:
    """Reproduce a light Worker snapshot inside a dark, then light, client."""
    root = Path(__file__).resolve().parents[1]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
        try:
            page = browser.new_page(viewport={"width": 390, "height": 800})
            page.set_content("""<html data-theme="dark"><body>
              <main class="main-panel"><div class="message assistant">
              <div class="message-body" id="reply"></div></div>
              <div class="composer"><textarea></textarea></div></main>
            </body></html>""")
            page.add_style_tag(path=str(root / "app" / "chat_client_v1.css"))
            page.add_script_tag(path=str(root / "app" / "chat_native_renderer_v1.js"))
            assert page.evaluate("""() => window.Chat2APINativeRendererV1.renderNativeSnapshot(
              document.querySelector('#reply'), {
                version:1, revision:1, finality:'verified-final', assets:[], files:[],
                root:{tag:'div',style:{color:'#111111',backgroundColor:'#ffffff',width:'1100px',marginLeft:'350px'},children:[
                  {tag:'p',text:'Native text',style:{color:'#111111'}},
                  {tag:'div',style:{display:'block',width:'900px',marginLeft:'300px'},children:[
                    {tag:'table',style:{width:'900px'},children:[{tag:'tbody',children:[
                      {tag:'tr',children:[{tag:'th',text:'Heading',style:{backgroundColor:'#ffffff',color:'#111111'}},
                        {tag:'td',text:'Value',style:{color:'#111111',whiteSpace:'nowrap',width:'800px'}}]}
                    ]}]}
                  ]}
                ]}
              })""")
            for theme, text_color, page_color, panel_color in (
                ("dark", "rgb(236, 236, 236)", "rgb(33, 33, 33)", "rgb(47, 47, 47)"),
                ("light", "rgb(37, 37, 37)", "rgb(255, 255, 255)", "rgb(244, 244, 244)"),
            ):
                page.evaluate("(theme) => document.documentElement.dataset.theme = theme", theme)
                assert page.locator(".native-rich-root p").evaluate("(el) => getComputedStyle(el).color") == text_color
                assert page.locator(".native-rich-root td").evaluate("(el) => getComputedStyle(el).color") == text_color
                assert page.locator(".native-rich-root th").evaluate("(el) => getComputedStyle(el).backgroundColor") == panel_color
                assert page.locator(".main-panel").evaluate("(el) => getComputedStyle(el).backgroundColor") == page_color
                assert page.locator(".composer textarea").evaluate("(el) => getComputedStyle(el).color") == text_color
                assert page.locator(".native-rich-root").evaluate("(el) => el.scrollWidth <= el.clientWidth + 1")
                assert page.locator(".native-table-scroll").evaluate("(el) => el.getBoundingClientRect().width <= 390")
        finally:
            browser.close()
