from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable


logger = logging.getLogger("chat2api.sse_transport")


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
        interval_seconds: float = 1.0,
    ) -> None:
        self.app = app
        self.interval_seconds = max(1.0, float(interval_seconds))

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return


        # Keepalive traffic is transport-only; it never writes assistant text
        # or asks the request-v6 Worker terminal owner to finish generation.
        started = time.monotonic()
        heartbeat_frames = 0
        body_frames = 0
        saw_http_disconnect = False
        stream_abort = False
        request_id = next((v.decode("latin-1", "replace") for k, v in scope.get("headers", [])
                           if k.lower() == b"x-chat2api-request-id"), "-")
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        async def queued_send(message: dict[str, Any]) -> None:
            await queue.put(message)

        async def tracked_receive() -> dict[str, Any]:
            nonlocal saw_http_disconnect
            message = await receive()
            if message.get("type") == "http.disconnect":
                saw_http_disconnect = True
            return message

        downstream = asyncio.create_task(self.app(scope, tracked_receive, queued_send))
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
                    heartbeat_frames += 1
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
                    if message.get("type") == "http.response.body":
                        body_frames += 1
                    continue

                # Downstream finished without another queued ASGI message. Surface
                # its exception instead of leaving the client hanging forever.
                if downstream in done and queue.empty():
                    await downstream
                    response_finished = True
        except asyncio.CancelledError:
            stream_abort = True
            raise
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
                await asyncio.gather(get_task, return_exceptions=True)
            if not downstream.done():
                downstream.cancel()
            await asyncio.gather(downstream, return_exceptions=True)
            if stream_open and not response_finished:
                logger.warning(
                    "Premature SSE closure request_id=%s elapsed_ms=%.1f "
                    "keepalive_frames=%d body_frames=%d http_disconnect_seen=%s "
                    "asgi_task_cancelled=%s interval_seconds=%.1f",
                    request_id, (time.monotonic() - started) * 1000,
                    heartbeat_frames, body_frames, saw_http_disconnect,
                    stream_abort, self.interval_seconds,
                )


def install_stream_keepalive_patch(app) -> None:
    # TX03 v0.22.217 evidence: repeated 2.8-2.9s ASGI stream cancels with
    # no Worker terminal. The prior 20s transport heartbeat could not protect
    # against a ~3s idle cutoff between client and Server.
    app.add_middleware(ChatSseKeepaliveMiddleware, interval_seconds=1.0)
