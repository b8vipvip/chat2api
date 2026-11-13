from __future__ import annotations

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
from app.user_console_v104_patch import install_user_console_v104_patch
from app.web_chat_integration_v1 import install_web_chat_features_v1


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_web_chat_document_attachment_upload_send_and_download_in_chromium(tmp_path: Path) -> None:
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    app = create_app(Settings(
        CHAT2API_DATA_DIR=tmp_path / "browser-e2e-data",
        CHAT2API_API_KEY="web-chat-browser-e2e-master-key",
        CHAT2API_ADMIN_USERNAME="web-chat-e2e-admin",
        CHAT2API_ADMIN_PASSWORD="web-chat-e2e-admin-password",
        CHAT2API_PAIRING_CODE="web-chat-browser-e2e-pairing-code",
        CHAT2API_PUBLIC_URL=base_url,
    ))
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
        assert ready, "Web Chat test server did not become ready within 30 seconds"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(accept_downloads=True)
            try:
                registered = context.request.post(
                    base_url + "/api/user/register",
                    headers={"Origin": base_url},
                    data={
                        "email": "document-browser-e2e@example.test",
                        "password": "document-browser-e2e-password",
                    },
                )
                assert registered.status == 200, registered.text()
                created = context.request.post(
                    base_url + "/api/user/keys",
                    headers={"Origin": base_url},
                    data={"name": "Browser E2E document key"},
                )
                assert created.status == 200, created.text()

                page = context.new_page()
                page.goto(base_url + "/chat", wait_until="domcontentloaded")
                page.wait_for_function(
                    "() => { const button = document.getElementById('fileButton'); "
                    "return button && !button.disabled; }",
                    timeout=15000,
                )
                assert page.locator("#fileInput").get_attribute("accept") == (
                    ".pdf,.txt,.md,.csv,.json,.rtf,.docx,.xlsx,.pptx"
                )

                document_bytes = b"Browser E2E document payload\nRevenue increased by 12 percent.\n"
                page.locator("#fileInput").set_input_files({
                    "name": "quarterly-summary.txt",
                    "mimeType": "text/plain",
                    "buffer": document_bytes,
                })
                page.locator(".attachment-file-chip").wait_for(state="visible", timeout=5000)
                assert page.locator(".attachment-file-name").inner_text() == "quarterly-summary.txt"
                page.locator("#messageInput").fill("Summarize the attached quarterly report.")

                captured: dict[str, object] = {}

                def complete_chat(route) -> None:
                    import json

                    payload = json.loads(route.request.post_data or "{}")
                    captured.update(payload)
                    route.fulfill(
                        status=200,
                        headers={"content-type": "text/event-stream; charset=utf-8", "cache-control": "no-cache"},
                        body=(
                            'data: {"choices":[{"index":0,"delta":{"content":"已读取季度报告。"},"finish_reason":null}]}\n\n'
                            "data: [DONE]\n\n"
                        ),
                    )

                page.route("**/v1/chat/completions", complete_chat)
                page.locator("#sendButton").click()
                page.locator("#messages .message-file-card").wait_for(state="visible", timeout=15000)
                page.get_by_text("已读取季度报告。").wait_for(state="visible", timeout=5000)

                messages = captured.get("messages")
                assert isinstance(messages, list) and messages
                content = messages[-1].get("content")
                assert isinstance(content, list)
                file_parts = [
                    part for part in content
                    if isinstance(part, dict) and part.get("type") == "file"
                ]
                assert len(file_parts) == 1
                file_part = file_parts[0]
                assert file_part["filename"] == "quarterly-summary.txt"
                assert file_part["mime_type"] == "text/plain"
                assert file_part["size"] == len(document_bytes)
                assert str(file_part["file_id"]).startswith("file_")

                with page.expect_download(timeout=10000) as download_info:
                    page.locator("#messages .message-file-card").click()
                download = download_info.value
                assert download.suggested_filename == "quarterly-summary.txt"
                downloaded_path = download.path()
                assert downloaded_path is not None
                assert Path(downloaded_path).read_bytes() == document_bytes
            finally:
                context.close()
                browser.close()
    finally:
        server.should_exit = True
        server_thread.join(timeout=10)
