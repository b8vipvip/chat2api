from __future__ import annotations

import asyncio

from app.stream_keepalive_patch import ChatSseKeepaliveMiddleware


def test_chat_sse_keepalive_is_emitted_during_quiet_stream() -> None:
    sent: list[dict] = []

    async def downstream(_scope, _receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/event-stream")],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'data: {"choices":[{"delta":{"role":"assistant"}}]}\n\n',
                "more_body": True,
            }
        )
        await asyncio.sleep(1.15)
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n", "more_body": False})

    async def receive():
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    middleware = ChatSseKeepaliveMiddleware(downstream, interval_seconds=1.0)
    asyncio.run(
        middleware(
            {"type": "http", "path": "/v1/chat/completions", "method": "POST"},
            receive,
            send,
        )
    )

    bodies = [message.get("body", b"") for message in sent if message.get("type") == "http.response.body"]
    assert b": chat2api-keepalive\n\n" in bodies
    assert bodies[-1] == b"data: [DONE]\n\n"


def test_non_chat_path_is_untouched() -> None:
    sent: list[dict] = []

    async def downstream(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(
        ChatSseKeepaliveMiddleware(downstream, interval_seconds=1.0)(
            {"type": "http", "path": "/healthz", "method": "GET"}, receive, send
        )
    )
    assert [item.get("body") for item in sent if item.get("type") == "http.response.body"] == [b"ok"]
