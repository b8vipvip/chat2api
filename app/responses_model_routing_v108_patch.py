from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from collections import OrderedDict
from typing import Any, Awaitable, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import model_capability_routing_patch as model_routing
from . import v13_patch
from .api_keys import ApiPrincipal
from .responses_emulated_tools_v109_patch import ResponsesEmulatedToolsMiddleware, _request_tools
from .responses_tool_stream_v111_patch import install_responses_tool_stream_v111_patch
from .responses_v108_patch import _decorate_prompt, _input_prompt, _tool_config
from .token_usage import usage_for


PATCH_REVISION = 108
DEFAULT_RESPONSES_MODEL = "gpt-5.6-sol"


def _headers(scope: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in scope.get("headers") or []:
        try:
            result[bytes(raw_name).decode("latin-1").lower()] = bytes(raw_value).decode("latin-1")
        except Exception:
            continue
    return result


def _ensure_request_id(scope: dict[str, Any]) -> tuple[dict[str, Any], str]:
    headers = list(scope.get("headers") or [])
    values = _headers(scope)
    raw = str(values.get("x-chat2api-request-id") or "").strip()
    request_id = raw if raw and len(raw) <= 128 else "resp_req_" + uuid.uuid4().hex
    if request_id == raw:
        return scope, request_id
    copied = dict(scope)
    copied["headers"] = [
        (name, value)
        for name, value in headers
        if bytes(name).decode("latin-1").lower() != "x-chat2api-request-id"
    ] + [(b"x-chat2api-request-id", request_id.encode("ascii"))]
    return copied, request_id


async def _principal(server_app: FastAPI, scope: dict[str, Any]) -> ApiPrincipal | None:
    values = _headers(scope)
    supplied = str(values.get("x-api-key") or "").strip()
    authorization = str(values.get("authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied:
        return None
    master = str(getattr(server_app.state.settings, "api_key", "") or "")
    if master and secrets.compare_digest(supplied, master):
        return ApiPrincipal(key_id="master", name="CHAT2API_API_KEY", kind="master", scopes=("admin", "chat", "models", "files", "images"))
    return await server_app.state.api_keys.authenticate(supplied)


def _owners(server_app: FastAPI) -> OrderedDict[str, str]:
    owners = getattr(server_app.state, "responses_response_owners", None)
    if not isinstance(owners, OrderedDict):
        owners = OrderedDict()
        server_app.state.responses_response_owners = owners
    return owners


def _owner_allows(server_app: FastAPI, principal: ApiPrincipal | None, response_id: str) -> bool:
    if principal is None or not response_id:
        return True
    owner = _owners(server_app).get(str(response_id))
    return not owner or owner == principal.key_id or principal.kind == "master"


async def _not_found(scope, receive, send) -> None:
    response = JSONResponse(
        {"error": {"message": "Response not found", "type": "invalid_request_error", "param": None, "code": "response_not_found"}},
        status_code=404,
    )
    await response(scope, receive, send)


def _prompt(payload: dict[str, Any]) -> str:
    try:
        prompt = _input_prompt(payload)
        native_tools, force = _tool_config(payload)
        return _decorate_prompt(prompt, native_tools, force)
    except Exception:
        return ""


def _response_payload(raw: bytes, stream: bool) -> dict[str, Any] | None:
    text = raw.decode("utf-8", errors="replace")
    if not stream:
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except (ValueError, TypeError):
            return None
    terminal: dict[str, Any] | None = None
    created: dict[str, Any] | None = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        kind = str(event.get("type") or "")
        response = event.get("response")
        if kind == "response.created" and isinstance(response, dict):
            created = response
        if kind in {"response.completed", "response.failed", "response.incomplete"} and isinstance(response, dict):
            terminal = response
    return terminal or created


class _SSEStreamObserver:
    """Observe delivered SSE frames without buffering the full stream."""

    MAX_FRAME_BYTES = 1024 * 1024

    def __init__(self) -> None:
        self.buffer = b""
        self.last_event: str | None = None
        self.terminal_event: str | None = None
        self.terminal_response: dict[str, Any] | None = None
        self.created_response: dict[str, Any] | None = None
        self.event_count = 0
        self.bytes_sent = 0
        self.frame_overflow = False
        self.tool_events: list[dict[str, Any]] = []
        self.tool_event_count = 0

    def feed(self, chunk: bytes) -> None:
        self.bytes_sent += len(chunk)
        self.buffer += chunk.replace(b"\r\n", b"\n")
        while b"\n\n" in self.buffer:
            frame, self.buffer = self.buffer.split(b"\n\n", 1)
            if len(frame) > self.MAX_FRAME_BYTES:
                self.frame_overflow = True
                continue
            event_name = ""
            data_lines: list[bytes] = []
            for line in frame.split(b"\n"):
                if line.startswith(b"event:"):
                    event_name = line[6:].strip().decode("utf-8", errors="replace")[:80]
                elif line.startswith(b"data:"):
                    data_lines.append(line[5:].strip())
            data = b"\n".join(data_lines)
            if not event_name and data:
                try:
                    event_name = str(json.loads(data).get("type") or "")[:80]
                except (ValueError, TypeError, AttributeError):
                    pass
            if event_name:
                self.last_event = event_name
                self.event_count += 1
            if event_name in {"response.output_item.added", "response.output_item.done"}:
                try:
                    tool_event = json.loads(data)
                except (ValueError, TypeError):
                    tool_event = None
                item = tool_event.get("item") if isinstance(tool_event, dict) else None
                if isinstance(item, dict) and str(item.get("type") or "") in {
                    "function_call", "custom_tool_call", "tool_search_call",
                }:
                    self.tool_event_count += 1
                    if len(self.tool_events) < 32:
                        self.tool_events.append({
                            "event": event_name,
                            "output_index": tool_event.get("output_index") if isinstance(
                                tool_event.get("output_index"), int
                            ) else None,
                            "type": str(item.get("type") or "")[:64],
                            "namespace": str(item.get("namespace") or "")[:120],
                            "name": str(item.get("name") or "")[:120],
                            "call_id": str(item.get("call_id") or "")[:128],
                            "item_id": str(item.get("id") or "")[:128],
                            "status": str(item.get("status") or "")[:64],
                        })
            if event_name not in {"response.created", "response.completed", "response.failed", "response.incomplete"}:
                continue
            try:
                event = json.loads(data)
            except (ValueError, TypeError):
                continue
            response = event.get("response") if isinstance(event, dict) else None
            if not isinstance(response, dict):
                continue
            if event_name == "response.created":
                self.created_response = {"id": response.get("id"), "status": response.get("status")}
            else:
                self.terminal_event = event_name
                self.terminal_response = response
        if len(self.buffer) > self.MAX_FRAME_BYTES:
            self.buffer = b""
            self.frame_overflow = True


def _responses_terminal_status(
    *, http_status: int, response_status: str, error_code: str,
    stream: bool, trace: dict[str, Any],
) -> tuple[str, str]:
    """The only decision point for caller-visible Responses request terminal state."""
    if http_status >= 400:
        return "error", "http_error"
    if response_status == "completed":
        return "completed", "response_completed"
    if response_status in {"failed", "incomplete"}:
        return "error", "timeout" if error_code == "timeout" else "response_" + response_status
    if stream:
        if trace.get("client_disconnected"):
            return "cancelled", "client_disconnect"
        if trace.get("send_failed"):
            return "cancelled", "downstream_send_failed"
        if trace.get("server_task_cancelled"):
            return "cancelled", "server_task_cancelled"
        if trace.get("transport_exception") == "TimeoutError":
            return "error", "timeout"
        if trace.get("transport_exception"):
            return "error", "stream_transport_exception"
        return "error", "missing_terminal_event"
    return "error", "missing_response_terminal"


def _usage(prompt: str, output_text: str, response: dict[str, Any] | None) -> dict[str, int | bool]:
    supplied = response.get("usage") if isinstance(response, dict) and isinstance(response.get("usage"), dict) else {}
    input_tokens = int(supplied.get("input_tokens") or 0)
    output_tokens = int(supplied.get("output_tokens") or 0)
    total_tokens = int(supplied.get("total_tokens") or 0)
    if input_tokens <= 0 and output_tokens <= 0 and (prompt or output_text):
        estimated = usage_for(prompt, output_text)
        input_tokens = estimated.prompt_tokens
        output_tokens = estimated.completion_tokens
        total_tokens = estimated.total_tokens
    if total_tokens <= 0:
        total_tokens = input_tokens + output_tokens
    return {
        "prompt_tokens": max(0, input_tokens),
        "completion_tokens": max(0, output_tokens),
        "total_tokens": max(0, total_tokens),
        "cached_input_tokens": 0,
        "estimated": True,
    }


async def _record_telemetry(
    server_app: FastAPI,
    *,
    request_id: str,
    principal: ApiPrincipal | None,
    payload: dict[str, Any],
    prompt: str,
    response: dict[str, Any] | None,
    http_status: int,
    started_mono: float,
    stream_trace: dict[str, Any] | None = None,
) -> None:
    if principal is None:
        return
    stream = bool(payload.get("stream", False))
    model = str(payload.get("model") or DEFAULT_RESPONSES_MODEL)
    response_id = str((response or {}).get("id") or "")
    output_text = str((response or {}).get("output_text") or "")
    response_status = str((response or {}).get("status") or "")
    error_value = (response or {}).get("error")
    error_text = ""
    if isinstance(error_value, dict):
        error_text = str(error_value.get("message") or error_value.get("code") or "")
    elif error_value:
        error_text = str(error_value)
    # Errors returned before a Worker is selected must remain diagnosable.
    # Preserve safe machine-readable response metadata rather than the old
    # opaque "HTTP 400"; never duplicate raw inputs or error message text in
    # diagnostic fields, since upstream errors may echo user content.
    error_code = ""
    error_param = ""
    error_type = ""
    if isinstance(error_value, dict):
        error_code = str(error_value.get("code") or "")[:80]
        error_param = str(error_value.get("param") or "")[:80]
        error_type = str(error_value.get("type") or "")[:80]
    error_reason = error_code or (
        "invalid-request" if http_status == 400 and error_type == "invalid_request_error"
        else "response-body-unavailable" if http_status >= 400 and not response
        else "http-error-unclassified" if http_status >= 400
        else ""
    )
    if response_id:
        owners = _owners(server_app)
        owners[response_id] = principal.key_id
        while len(owners) > 512:
            owners.popitem(last=False)
    trace = stream_trace or {}
    status, terminal_reason = _responses_terminal_status(
        http_status=http_status, response_status=response_status,
        error_code=error_code, stream=stream, trace=trace,
    )
    tools = _request_tools(payload)
    tool_types = [str(item.get("type") or "") for item in tools if isinstance(item, dict) and item.get("type")]
    # Request-v6/Broker records the browser's first token and selected Worker.
    # Responses' outer HTTP middleware owns the public terminal status, but
    # must merge these existing measurements instead of overwriting them with
    # a total-only timing dict or a null client_id.
    getter = getattr(server_app.state.telemetry, "get", None)
    observed = (getter(request_id) if callable(getter) else None) or {}
    observed_timings = observed.get("timings")
    if not isinstance(observed_timings, dict):
        observed_timings = {}
    timings = {**observed_timings, "total_ms": round((time.perf_counter() - started_mono) * 1000, 1)}
    routed_client = observed.get("worker_client_id") or observed.get("client_id") or trace.get("worker_client_id")
    observed_diagnostics = observed.get("diagnostics")
    if not isinstance(observed_diagnostics, dict):
        observed_diagnostics = {}
    await server_app.state.telemetry.upsert(
        {
            "request_id": request_id,
            "response_id": response_id or None,
            "client_id": routed_client,
            "worker_client_id": routed_client,
            "worker_request_id": trace.get("worker_request_id") or observed.get("worker_request_id"),
            "worker_attempt_ids": trace.get("worker_attempt_ids") or observed.get("worker_attempt_ids") or [],
            "api_key_id": principal.key_id,
            "api_key_name": principal.name,
            "auth_kind": principal.kind,
            "requested_model": model,
            "request_type": "responses",
            "attachments_count": 0,
            "stream": stream,
            "prompt_mode": "responses-v108",
            "prompt_chars": len(prompt),
            "completion_chars": len(output_text),
            "status": status,
            "usage": _usage(prompt, output_text, response),
            "timings": timings,
            "diagnostics": {
                **observed_diagnostics,
                "response_protocol": "responses-v108",
                "responses_patch_revision": PATCH_REVISION,
                "responses_tool_stream_revision": int(getattr(server_app.state, "responses_tool_stream_revision", 0) or 0),
                "tool_types": tool_types,
                "response_declared_tools": [
                    {
                        "type": str(item.get("type") or "")[:64],
                        "namespace": str(item.get("namespace") or "")[:120],
                        "name": str(item.get("name") or "")[:120],
                    }
                    for item in tools[:32] if isinstance(item, dict)
                ],
                "response_http_status": http_status,
                "response_request_timeout_seconds": payload.get("timeout") if isinstance(
                    payload.get("timeout"), (int, float)
                ) else None,
                "response_server_default_timeout_seconds": getattr(
                    server_app.state.settings, "request_timeout_seconds", None
                ),

                "response_error_code": error_code or None,
                "response_error_param": error_param or None,
                "response_error_type": error_type or None,
                "response_error_reason": error_reason or None,
                "response_object_created": bool(response_id),
                "response_stream_terminal_revision": 156,
                "response_stream_termination_reason": terminal_reason,
                "response_stream_last_sse_event": trace.get("last_sse_event"),
                "response_stream_terminal_event": trace.get("terminal_event"),
                "response_stream_event_count": trace.get("event_count", 0),
                "response_stream_bytes_sent": trace.get("bytes_sent", 0),
                "response_stream_body_complete": bool(trace.get("body_complete")),
                "response_stream_client_disconnect": bool(trace.get("client_disconnected")),
                "response_stream_server_task_cancelled": bool(trace.get("server_task_cancelled")),
                "response_stream_send_failed": bool(trace.get("send_failed")),
                "response_stream_transport_exception": trace.get("transport_exception"),
                "response_stream_disconnect_after_ms": trace.get("disconnect_after_ms"),
                "response_stream_frame_overflow": bool(trace.get("frame_overflow")),
                "response_stream_tool_event_count": trace.get("tool_event_count", 0),
                "response_stream_tool_events": trace.get("tool_events", []),
                "worker_request_id": trace.get("worker_request_id") or observed.get("worker_request_id"),
                "worker_attempt_ids": trace.get("worker_attempt_ids") or observed.get("worker_attempt_ids") or [],
            },
            "error": error_text or (
                f"HTTP {http_status} ({error_reason})" if http_status >= 400
                else f"Responses stream ended: {terminal_reason}" if status != "completed" and stream
                else None
            ),
        }
    )


class _ResponsesModelContextMiddleware:
    """Route Responses through the existing model resolver, ownership and metering boundary."""

    def __init__(self, app: Callable[..., Awaitable[None]], server_app: FastAPI) -> None:
        self.app = app
        self.server_app = server_app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        path = str(scope.get("path") or "")
        method = str(scope.get("method") or "")
        if scope.get("type") == "http" and method == "GET" and path.startswith("/v1/responses/"):
            principal = await _principal(self.server_app, scope)
            response_id = path.rsplit("/", 1)[-1]
            if not _owner_allows(self.server_app, principal, response_id):
                await _not_found(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        if scope.get("type") != "http" or method != "POST" or scope.get("path") != "/v1/responses":
            await self.app(scope, receive, send)
            return

        scope, request_id = _ensure_request_id(scope)
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message.get("type") == "http.disconnect":
                await self.app(scope, receive, send)
                return
            chunks.append(bytes(message.get("body") or b""))
            if not message.get("more_body", False):
                break
        raw = b"".join(chunks)
        sent_body = False

        async def replay_receive():
            nonlocal sent_body
            if not sent_body:
                sent_body = True
                return {"type": "http.request", "body": raw, "more_body": False}
            message = await receive()
            if message.get("type") == "http.disconnect":
                trace = scope.get("chat2api.responses_trace")
                if isinstance(trace, dict):
                    trace["client_disconnected"] = True
                    trace["disconnect_after_ms"] = round(
                        (time.perf_counter() - trace["started_mono"]) * 1000, 1
                    )
            return message

        payload: dict[str, Any] = {}
        target: dict[str, Any] | None = None
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
            if isinstance(parsed, dict):
                payload = parsed
                model = str(payload.get("model") or DEFAULT_RESPONSES_MODEL).strip().lower()
                target = {"model": model, "needs_multimodal": False}
        except (UnicodeDecodeError, ValueError, TypeError):
            target = None

        principal = await _principal(self.server_app, scope)
        previous_response_id = str(payload.get("previous_response_id") or "").strip()
        if previous_response_id and not _owner_allows(self.server_app, principal, previous_response_id):
            await _not_found(scope, replay_receive, send)
            return
        prompt = _prompt(payload)
        started_mono = time.perf_counter()
        status_code = 500
        response_chunks: list[bytes] = []
        observer = _SSEStreamObserver() if bool(payload.get("stream", False)) else None
        trace: dict[str, Any] = {"worker_attempt_ids": [], "started_mono": started_mono}
        scope["chat2api.responses_trace"] = trace

        async def observed_send(message):
            nonlocal status_code
            try:
                await send(message)
            except OSError:
                trace["send_failed"] = True
                raise
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 500)
            elif message.get("type") == "http.response.body":
                chunk = bytes(message.get("body") or b"")
                if observer is not None:
                    observer.feed(chunk)
                else:
                    response_chunks.append(chunk)
                if not message.get("more_body", False):
                    trace["body_complete"] = True

        local_token = model_routing._MODEL_CONTEXT.set(target)
        historical_token = v13_patch._target_context.set(target)
        try:
            await self.app(scope, replay_receive, observed_send)
        except BaseException as error:
            trace["transport_exception"] = type(error).__name__
            if isinstance(error, asyncio.CancelledError):
                trace["server_task_cancelled"] = not bool(trace.get("client_disconnected"))
            raise
        finally:
            v13_patch._target_context.reset(historical_token)
            model_routing._MODEL_CONTEXT.reset(local_token)
            try:
                response = (
                    observer.terminal_response or observer.created_response
                    if observer is not None
                    else _response_payload(b"".join(response_chunks), False)
                )
                if observer is not None:
                    trace.update({
                        "last_sse_event": observer.last_event,
                        "terminal_event": observer.terminal_event,
                        "event_count": observer.event_count,
                        "bytes_sent": observer.bytes_sent,
                        "frame_overflow": observer.frame_overflow,
                        "tool_event_count": observer.tool_event_count,
                        "tool_events": observer.tool_events,
                    })
                await _record_telemetry(
                    self.server_app,
                    request_id=request_id,
                    principal=principal,
                    payload=payload,
                    prompt=prompt,
                    response=response,
                    http_status=status_code,
                    started_mono=started_mono,
                    stream_trace=trace,
                )
            except Exception:
                # Telemetry must never break the API response path. The canonical
                # request handler remains authoritative even if metering storage is
                # temporarily unavailable.
                pass


def install_responses_model_routing_v108_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_model_routing_v108_installed", False):
        return app
    # Normalize emulated function/custom tool streams before the middleware is
    # installed. Older Codex runtimes rely on the argument/input delta+done
    # lifecycle even when output_item.done already carries the completed item.
    install_responses_tool_stream_v111_patch(app)
    # Install the emulated tool middleware first, then the model/telemetry owner.
    # Starlette inserts later middleware on the outside, so every emulated tool
    # response remains inside the canonical routing + ownership + billing boundary.
    app.add_middleware(ResponsesEmulatedToolsMiddleware, server_app=app)
    app.add_middleware(_ResponsesModelContextMiddleware, server_app=app)
    app.state.responses_model_routing_v108_installed = True
    return app
