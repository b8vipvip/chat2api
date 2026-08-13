from __future__ import annotations

import asyncio
import contextvars
import json
import secrets
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response


PATCH_VERSION = "0.19.0"
_TEXT_PATHS = {"/v1/chat/completions", "/v1/responses", "/v1/completions"}
_http_request_marker: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "chat2api_v19_http_request_marker", default=None
)


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def _is_nonstream_json(body: bytes) -> bool:
    try:
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:
        return True
    return not bool(payload.get("stream")) if isinstance(payload, dict) else True


def install_v19_patch(app: FastAPI) -> FastAPI:
    registry = app.state.registry
    broker = app.state.broker
    app.version = PATCH_VERSION

    tracked_states: dict[str, Any] = {}
    base_create = broker.create

    if not getattr(broker, "_chat2api_v19_disconnect_tracking", False):
        async def create_with_http_tracking(request_id: str, client_id: str):
            state = await base_create(request_id, client_id)
            marker = _http_request_marker.get()
            if marker:
                tracked_states[marker] = state
            return state

        broker.create = create_with_http_tracking
        broker._chat2api_v19_disconnect_tracking = True
        broker._chat2api_v19_tracked_states = tracked_states
    else:
        tracked_states = getattr(broker, "_chat2api_v19_tracked_states", tracked_states)

    async def call_with_disconnect_guard(request: Request, call_next):
        # Cache the JSON body before starting the downstream task. Starlette can then
        # replay it to FastAPI while this middleware safely polls http.disconnect.
        body = await request.body()
        if not _is_nonstream_json(body):
            return await call_next(request)

        marker = secrets.token_hex(12)
        token = _http_request_marker.set(marker)
        try:
            response_task = asyncio.create_task(call_next(request))
        finally:
            # create_task copied the context, so the downstream broker.create still
            # sees marker while unrelated requests do not inherit it here.
            _http_request_marker.reset(token)

        disconnected = False
        try:
            while True:
                done, _ = await asyncio.wait({response_task}, timeout=0.25)
                if done:
                    return response_task.result()
                if await request.is_disconnected():
                    disconnected = True
                    state = tracked_states.get(marker)
                    if state is not None:
                        state.completed_mono = state.completed_mono or time.perf_counter()
                        state.diagnostics["api_client_disconnected"] = True
                        state.diagnostics["disconnect_cleanup"] = "v19-cancel-and-release"
                        try:
                            await registry.send(
                                state.client_id,
                                {"type": "chat.cancel", "request_id": state.request_id},
                            )
                        except Exception:
                            pass
                        future = getattr(state, "final_future", None)
                        if future is not None and not future.done():
                            future.set_exception(RuntimeError("API client disconnected"))
                    try:
                        return await asyncio.wait_for(response_task, timeout=1.5)
                    except asyncio.TimeoutError:
                        response_task.cancel()
                        await asyncio.gather(response_task, return_exceptions=True)
                        return Response(status_code=499)
        finally:
            tracked_states.pop(marker, None)
            if disconnected and not response_task.done():
                response_task.cancel()

    async def rewrite_version(request: Request, response):
        path = request.url.path
        content_type = response.headers.get("content-type", "")
        should_rewrite = (
            "application/json" in content_type
            and (
                path in {"/", "/healthz"}
                or path.startswith("/api/admin/")
            )
        )
        if not should_rewrite:
            return response
        raw = await _response_bytes(response)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            return Response(raw, status_code=response.status_code, media_type="application/json")
        if isinstance(payload, dict):
            payload["version"] = PATCH_VERSION
            if path == "/api/admin/overview":
                capabilities = payload.setdefault("capabilities", {})
                if isinstance(capabilities, dict):
                    capabilities["nonstream_disconnect_cleanup"] = True
                    capabilities["stale_stop_completion_recovery"] = True
                    capabilities["conversation_warm_pool"] = True
            if path.startswith("/api/admin/requests/") and path.endswith("/log"):
                payload["server_version"] = PATCH_VERSION
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store"
        return JSONResponse(payload, status_code=response.status_code, headers=headers)

    @app.middleware("http")
    async def v19_completion_and_disconnect_guard(request: Request, call_next):
        if request.method == "POST" and request.url.path in _TEXT_PATHS:
            response = await call_with_disconnect_guard(request, call_next)
        else:
            response = await call_next(request)
        return await rewrite_version(request, response)

    return app
