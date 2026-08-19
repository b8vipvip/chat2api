from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable


class ChatSseKeepaliveMiddleware:
    """Emit standards-compliant SSE comments while browser-backed chat is quiet.

    Multimodal requests can legitimately spend tens of seconds preparing an
    attachment in the ChatGPT composer before the first assistant token exists.
    OpenAI-compatible clients and reverse proxies should not interpret that quiet
    preparation window as a dead connection, so keep the transport alive without
    fabricating assistant content.
    """

    def __init__(
        self,
        app: Callable[..., Awaitable[Any]],
        interval_seconds: float = 20.0,
    ) -> None:
        self.app = app
        self.interval_seconds = max(1.0, float(interval_seconds))

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def queued_send(message: dict[str, Any]) -> None:
            await queue.put(message)

        downstream = asyncio.create_task(self.app(scope, receive, queued_send))
        stream_open = False
        response_finished = False
        get_task: asyncio.Task[dict[str, Any]] | None = None
        try:
            while not response_finished:
                if get_task is None:
                    get_task = asyncio.create_task(queue.get())

                wait_set: set[asyncio.Task[Any]] = {get_task, downstream}
                done, _ = await asyncio.wait(
                    wait_set,
                    timeout=self.interval_seconds if stream_open else None,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if not done:
                    # SSE comments are legal keepalives and are ignored by OpenAI
                    # parsers. They reset network idle timers without becoming model
                    # output or changing completion text.
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b": chat2api-keepalive\n\n",
                            "more_body": True,
                        }
                    )
                    continue

                if get_task in done:
                    message = get_task.result()
                    get_task = None
                    if message.get("type") == "http.response.start":
                        content_type = ""
                        for key, value in message.get("headers", []):
                            if key.lower() == b"content-type":
                                content_type = value.decode("latin-1", errors="ignore").lower()
                                break
                        stream_open = "text/event-stream" in content_type
                    elif message.get("type") == "http.response.body":
                        response_finished = not bool(message.get("more_body", False))
                    await send(message)
                    continue

                # Downstream finished without another queued ASGI message. Surface
                # its exception instead of leaving the client hanging forever.
                if downstream in done and queue.empty():
                    await downstream
                    response_finished = True
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
                await asyncio.gather(get_task, return_exceptions=True)
            if not downstream.done():
                downstream.cancel()
            await asyncio.gather(downstream, return_exceptions=True)


def install_stream_keepalive_patch(app) -> None:
    app.add_middleware(ChatSseKeepaliveMiddleware, interval_seconds=20.0)
