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



def test_three_second_idle_cutoff_is_prevented_by_transport_heartbeat() -> None:
    """A quiet Worker generation must receive at least two frames by 2.8s."""
    import time

    sent: list[tuple[float, bytes]] = []

    async def downstream(_scope, _receive, send):
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        await send({
            "type": "http.response.body", "body": b'data: {"choices":[]}\\n\\n',
            "more_body": True,
        })
        await asyncio.sleep(3.1)
        await send({"type": "http.response.body", "body": b"data: [DONE]\\n\\n", "more_body": False})

    async def receive():
        await asyncio.sleep(60)
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            sent.append((time.monotonic(), message["body"]))

    async def run():
        await ChatSseKeepaliveMiddleware(downstream, interval_seconds=1.0)(
            {"type": "http", "path": "/v1/chat/completions", "method": "POST"},
            receive, send,
        )

    asyncio.run(run())
    heartbeats = [(at, body) for at, body in sent if body.startswith(b": chat2api-keepalive")]
    assert len(heartbeats) >= 2, heartbeats
    assert heartbeats[1][0] - sent[0][0] < 2.8
    assert sent[-1][1] == b"data: [DONE]\\n\\n"


def test_production_keepalive_is_one_second_and_abort_is_diagnosed(caplog) -> None:
    import inspect
    import pytest
    from app.stream_keepalive_patch import install_stream_keepalive_patch

    assert "interval_seconds=1.0" in inspect.getsource(install_stream_keepalive_patch)
    async def downstream(_scope, _receive, send):
        await send({
            "type": "http.response.start", "status": 200,
            "headers": [(b"content-type", b"text/event-stream")],
        })
        await send({
            "type": "http.response.body", "body": b'data: {"choices":[]}\\n\\n',
            "more_body": True,
        })
        await asyncio.sleep(40)

    async def receive():
        await asyncio.sleep(40)
        return {"type": "http.disconnect"}

    async def send(message):
        if message.get("body", b"").startswith(b": chat2api-keepalive"):
            raise ConnectionError("simulated peer disconnected")

    async def run():
        await ChatSseKeepaliveMiddleware(downstream, interval_seconds=1.0)(
            {
                "type": "http", "path": "/v1/chat/completions", "method": "POST",
                "headers": [(b"x-chat2api-request-id", b"req_earlydisconnect1234")],
            },
            receive, send,
        )

    with caplog.at_level("WARNING", logger="chat2api.sse_transport"):
        with pytest.raises(ConnectionError, match="simulated"):
            asyncio.run(run())
    assert "Premature SSE closure request_id=req_earlydisconnect1234" in caplog.text
    assert "keepalive_frames=0" in caplog.text
    assert "body_frames=1" in caplog.text
