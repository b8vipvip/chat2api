import asyncio
import json
from types import SimpleNamespace

from fastapi.responses import JSONResponse

import app.responses_emulated_tools_v109_patch as bridge


def run_asgi(body: dict) -> tuple[list[dict], int]:
    sent: list[dict] = []
    fallback_calls = 0
    request_messages = [
        {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive():
        if request_messages:
            return request_messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    async def fallback(scope, receive, send):
        nonlocal fallback_calls
        fallback_calls += 1
        response = JSONResponse({"fallback": True})
        await response(scope, receive, send)

    async def fake_handle(app, request, parsed):
        return JSONResponse({"bridge": True, "tool_type": parsed["tools"][0]["type"]})

    original = bridge._handle
    bridge._handle = fake_handle
    try:
        middleware = bridge.ResponsesEmulatedToolsMiddleware(fallback, SimpleNamespace())
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/responses",
            "headers": [(b"content-type", b"application/json")],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
            "http_version": "1.1",
        }
        asyncio.run(middleware(scope, receive, send))
    finally:
        bridge._handle = original
    return sent, fallback_calls


def response_json(sent: list[dict]) -> dict:
    raw = b"".join(bytes(message.get("body") or b"") for message in sent if message.get("type") == "http.response.body")
    return json.loads(raw.decode())


def test_function_tool_request_is_intercepted_by_emulated_bridge() -> None:
    sent, fallback_calls = run_asgi(
        {"model": "gpt-5.6-sol", "tools": [{"type": "function", "name": "exec_command", "parameters": {}}]}
    )
    assert fallback_calls == 0
    assert response_json(sent) == {"bridge": True, "tool_type": "function"}


def test_native_web_search_request_stays_on_v108_path() -> None:
    sent, fallback_calls = run_asgi(
        {"model": "gpt-5.6-sol", "tools": [{"type": "web_search"}]}
    )
    assert fallback_calls == 1
    assert response_json(sent) == {"fallback": True}
