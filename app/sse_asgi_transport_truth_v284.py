"""Request-owned ASGI transport evidence for /v1/chat/completions SSE.

Observe only the receive/send message *boundaries*. The Browser/Worker Broker
still solely decides generation terminal. The server cannot infer that a
successful ASGI send implies client-side TCP receipt or application acceptance.

No request/response bodies, prompts, media URLs, headers or credentials are
stored. In particular a raw SSE chunk is inspected only for the exact [DONE]
frame; never persisted or logged. Normal JSON and unrelated APIs are untouched.
"""
from __future__ import annotations

import logging
import time
from typing import Any

LOG = logging.getLogger("chat2api.sse_transport")



class _DoneFrameDetector:
    """Bounded SSE *event* parser; no response content is retained in telemetry.

    ASGI may coalesce several events into one body message or split a terminal
    event across sends. Only an entire SSE event containing exactly one
    'data: [DONE]' field counts, not the same bytes embedded in JSON text.
    Keep at most 64 transient bytes from any line, even for large responses.
    """

    _MAX_LINE_PREFIX = 64

    def __init__(self) -> None:
        self._line = bytearray()
        self._line_overflow = False
        self._data_line_count = 0
        self._done_data_only = False
        self.done = False

    def _append(self, fragment: bytes) -> None:
        if self._line_overflow:
            return
        if len(fragment) > self._MAX_LINE_PREFIX - len(self._line):
            self._line.clear()
            self._line_overflow = True
            return
        self._line.extend(fragment)

    def _finish_line(self) -> None:
        if self._line_overflow:
            # An uninspected oversized line might contain another data field;
            # reject the candidate event rather than declaring false success.
            self._data_line_count += 1
            self._done_data_only = False
        else:
            line = bytes(self._line).removesuffix(b"\r")
            if not line:
                if self._data_line_count == 1 and self._done_data_only:
                    self.done = True
                self._data_line_count = 0
                self._done_data_only = False
            elif line.startswith(b"data:") or line == b"data":
                value = line[5:] if line.startswith(b"data:") else b""
                if value.startswith(b" "):
                    value = value[1:]
                self._data_line_count += 1
                self._done_data_only = (
                    self._data_line_count == 1 and value == b"[DONE]"
                )
        self._line.clear()
        self._line_overflow = False

    def feed(self, body: bytes) -> bool:
        if self.done:
            return True
        offset = 0
        while offset < len(body):
            end = body.find(b"\n", offset)
            if end < 0:
                self._append(body[offset:])
                break
            self._append(body[offset:end])
            self._finish_line()
            offset = end + 1
        return self.done


class SSETransportTruth:
    def __init__(self, app: Any, telemetry: Any):
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: dict, receive, send) -> None:
        if scope.get("type") != "http" or scope.get("path") != "/v1/chat/completions":
            await self.app(scope, receive, send)
            return

        started = time.monotonic()
        truth = {
            "http_disconnect_seen": False,
            "http_disconnect_ms": None,
            "asgi_receive_calls": 0,
            "asgi_receive_messages": 0,
            "asgi_receive_last_ms": None,
            "asgi_response_started_ms": None,
            "asgi_first_body_ms": None,
            "asgi_last_body_ms": None,
            "asgi_body_chunks_sent": 0,
            "asgi_body_bytes_sent": 0,
            "asgi_done_frame_sent": False,
            "asgi_final_body_sent": False,
            "asgi_send_error": None,
            "asgi_response_status": None,
        }
        # Starlette's StreamingResponse can cancel its body iterator after a
        # peer disconnect. The request handler and this receive wrapper share
        # the same ASGI scope, so the cancellation path sees actual evidence.
        scope["chat2api_sse_transport_truth"] = truth
        is_sse = False
        done_detector = _DoneFrameDetector()

        def elapsed_ms() -> int:
            return max(0, round((time.monotonic() - started) * 1000))

        async def observed_receive():
            # No receive call means disconnect absence was NOT checked.
            # Count message boundaries only, never inspect request payloads.
            truth["asgi_receive_calls"] += 1
            message = await receive()
            truth["asgi_receive_messages"] += 1
            truth["asgi_receive_last_ms"] = elapsed_ms()
            if message.get("type") == "http.disconnect":
                truth["http_disconnect_seen"] = True
                truth["http_disconnect_ms"] = elapsed_ms()
            return message

        async def observed_send(message):
            nonlocal is_sse
            kind = message.get("type")
            if kind == "http.response.start":
                headers = message.get("headers") or []
                is_sse = any(
                    isinstance(k, bytes) and k.lower() == b"content-type"
                    and isinstance(v, bytes)
                    and v.lower().startswith(b"text/event-stream")
                    for k, v in headers
                )
                if is_sse:
                    truth["asgi_response_started_ms"] = elapsed_ms()
                    truth["asgi_response_status"] = message.get("status")
            try:
                # Count ONLY body bytes successfully passed to the actual ASGI
                # send hook. Never store or log a response body.
                await send(message)
            except BaseException as exc:
                if is_sse:
                    truth["asgi_send_error"] = type(exc).__name__[:60]
                raise
            if is_sse and kind == "http.response.body":
                body = message.get("body") or b""
                truth["asgi_body_chunks_sent"] += 1
                truth["asgi_body_bytes_sent"] += len(body)
                truth["asgi_first_body_ms"] = (
                    truth["asgi_first_body_ms"]
                    if truth["asgi_first_body_ms"] is not None else elapsed_ms()
                )
                truth["asgi_last_body_ms"] = elapsed_ms()
                # Observe framing across coalesced or split ASGI sends, but
                # never persist a chunk, prompt, token or even a line excerpt.
                if isinstance(body, bytes) and done_detector.feed(body):
                    truth["asgi_done_frame_sent"] = True
                if not message.get("more_body", False):
                    truth["asgi_final_body_sent"] = True

        try:
            await self.app(scope, observed_receive, observed_send)
        finally:
            if is_sse:
                await self._persist_truth(scope, truth)

    async def _persist_truth(self, scope: dict, truth: dict) -> None:
        if truth["http_disconnect_seen"] and not truth["asgi_done_frame_sent"]:
            delivery_state = "disconnected"
        elif truth["asgi_send_error"]:
            delivery_state = "asgi-send-error"
        elif truth["asgi_done_frame_sent"] and truth["asgi_final_body_sent"]:
            # ASGI send acceptance is NOT equivalent to client receipt.
            delivery_state = "asgi-complete-unconfirmed"
        else:
            delivery_state = "asgi-incomplete"

        # The request-owned Broker state survives detached HTTP close.
        state = scope.get("chat2api_sse_broker_state")
        if state is not None and isinstance(getattr(state, "diagnostics", None), dict):
            state.diagnostics["api_stream_peer_disconnect_seen"] = truth["http_disconnect_seen"]
            state.diagnostics["api_delivery_state"] = delivery_state
            state.diagnostics["api_stream_transport_truth"] = dict(truth)

        request_id = scope.get("chat2api_sse_request_id")
        if not isinstance(request_id, str) or not request_id:
            return
        try:
            row = self.telemetry.get(request_id)
            if row is None:
                return
            prior = row.get("diagnostics")
            diagnostics = dict(prior) if isinstance(prior, dict) else {}
            diagnostics.update({
                "api_stream_peer_disconnect_seen": truth["http_disconnect_seen"],
                "api_delivery_state": delivery_state,
                "api_stream_transport_truth": dict(truth),
            })
            await self.telemetry.update(request_id, {"diagnostics": diagnostics})
        except Exception:
            # Optional evidence MUST NOT mask the upstream ASGI transport
            # failure or override the Worker/Broker authoritative terminal.
            LOG.exception("Cannot persist SSE transport evidence request_id=%s", request_id)
