from __future__ import annotations

import json
import secrets
import time
import uuid
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from . import model_capability_routing_patch as model_routing
from . import v13_patch
from .api_keys import ApiPrincipal
from .responses_emulated_tools_v109_patch import ResponsesEmulatedToolsMiddleware
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
    if http_status >= 400:
        status = "error"
    elif response_status == "completed":
        status = "completed"
    elif response_status in {"failed", "incomplete"}:
        status = "error"
    elif stream:
        status = "cancelled"
    else:
        status = "error"
    tools = payload.get("tools") if isinstance(payload.get("tools"), list) else []
    tool_types = [str(item.get("type") or "") for item in tools if isinstance(item, dict) and item.get("type")]
    await server_app.state.telemetry.upsert(
        {
            "request_id": request_id,
            "response_id": response_id or None,
            "client_id": None,
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
            "timings": {"total_ms": round((time.perf_counter() - started_mono) * 1000, 1)},
            "diagnostics": {
                "response_protocol": "responses-v108",
                "responses_patch_revision": PATCH_REVISION,
                "tool_types": tool_types,
            },
            "error": error_text or (f"HTTP {http_status}" if http_status >= 400 else None),
        }
    )


class _ResponsesModelContextMiddleware:
    """Route Responses through the existing model resolver and metering boundary.

    The historical routing middleware only owns /v1/chat/completions. Responses
    requests must carry the same model ContextVar and must also flow through the
    canonical telemetry.upsert path so request history and user billing cannot be
    bypassed by choosing /v1/responses.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], server_app: FastAPI) -> None:
        self.app = app
        self.server_app = server_app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/v1/responses":
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
            return await receive()

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
        prompt = _prompt(payload)
        started_mono = time.perf_counter()
        status_code = 500
        response_chunks: list[bytes] = []

        async def observed_send(message):
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 500)
            elif message.get("type") == "http.response.body":
                response_chunks.append(bytes(message.get("body") or b""))
            await send(message)

        local_token = model_routing._MODEL_CONTEXT.set(target)
        historical_token = v13_patch._target_context.set(target)
        try:
            await self.app(scope, replay_receive, observed_send)
        finally:
            v13_patch._target_context.reset(historical_token)
            model_routing._MODEL_CONTEXT.reset(local_token)
            try:
                response = _response_payload(b"".join(response_chunks), bool(payload.get("stream", False)))
                await _record_telemetry(
                    self.server_app,
                    request_id=request_id,
                    principal=principal,
                    payload=payload,
                    prompt=prompt,
                    response=response,
                    http_status=status_code,
                    started_mono=started_mono,
                )
            except Exception:
                # Telemetry must never break the API response path. The canonical
                # request handler remains authoritative even if metering storage is
                # temporarily unavailable.
                pass


def install_responses_model_routing_v108_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_model_routing_v108_installed", False):
        return app
    # Install the emulated tool middleware first, then the model/telemetry owner.
    # Starlette inserts later middleware on the outside, so every emulated tool
    # response remains inside the canonical routing + billing boundary.
    app.add_middleware(ResponsesEmulatedToolsMiddleware, server_app=app)
    app.add_middleware(_ResponsesModelContextMiddleware, server_app=app)
    app.state.responses_model_routing_v108_installed = True
    return app
