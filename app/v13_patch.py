from __future__ import annotations

import contextvars
import json
import secrets
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .api_keys import ApiPrincipal
from .models import ChatCompletionRequest, ChatMessage


PATCH_VERSION = "0.13.0"
DEFAULT_TEXT_MODEL = "gpt-5.6-sol"
TEXT_MODELS = ("gpt-5.6-sol", "gpt-5.5")
SPECIAL_MODELS = ("gpt-image", "gpt-live", "gpt-live-mini")

_target_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "chat2api_v13_target", default=None
)


REASONING_ALIASES: dict[str, tuple[str, str]] = {
    "极速": ("instant", "low"),
    "instant": ("instant", "low"),
    "fast": ("instant", "low"),
    "minimal": ("instant", "low"),
    "low": ("instant", "low"),
    "none": ("instant", "low"),
    "中": ("medium", "medium"),
    "medium": ("medium", "medium"),
    "高": ("high", "high"),
    "high": ("high", "high"),
    "xhigh": ("high", "high"),
}


def _openai_error(message: str, *, param: str | None = None, code: str = "invalid_request_error", status_code: int = 400) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error", "param": param, "code": code}},
        status_code=status_code,
    )


def _normalize_model(value: Any) -> str:
    raw = str(value or DEFAULT_TEXT_MODEL).strip().lower()
    if raw in {"default", "chatgpt-web"}:
        raise ValueError("model IDs 'default' and 'chatgpt-web' were removed; use 'gpt-5.6-sol' or 'gpt-5.5'")
    if raw not in TEXT_MODELS:
        raise ValueError(f"Unsupported text model: {raw}. Supported models: {', '.join(TEXT_MODELS)}")
    return raw


def _normalize_reasoning(value: Any) -> tuple[str, str] | tuple[None, None]:
    if value is None or str(value).strip() == "":
        return None, None
    raw = str(value).strip().lower()
    normalized = REASONING_ALIASES.get(raw)
    if not normalized:
        raise ValueError("Unsupported reasoning effort. Use low/medium/high (极速/中/高).")
    return normalized


def _target_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    model = _normalize_model(payload.get("model") or DEFAULT_TEXT_MODEL)
    direct = payload.get("reasoning_effort")
    nested = payload.get("reasoning")
    nested_value = nested.get("effort") if isinstance(nested, dict) else None
    if direct is not None and nested_value is not None:
        direct_pair = _normalize_reasoning(direct)
        nested_pair = _normalize_reasoning(nested_value)
        if direct_pair != nested_pair:
            raise ValueError("reasoning_effort and reasoning.effort specify different values")
        reasoning_level, reasoning_effort = direct_pair
    else:
        reasoning_level, reasoning_effort = _normalize_reasoning(nested_value if nested_value is not None else direct)
    return {
        "model": model,
        "reasoning_level": reasoning_level,
        "reasoning_effort": reasoning_effort,
    }


def _usage_for_responses(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
    output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": int(usage.get("total_tokens") or input_tokens + output_tokens),
    }


def _content_part(part: Any) -> Any:
    if not isinstance(part, dict):
        return part
    value = dict(part)
    kind = str(value.get("type") or "")
    if kind == "input_image" and value.get("file_id") and not value.get("image_url"):
        value["image_url"] = str(value["file_id"])
    return value


def _responses_messages(input_value: Any, instructions: Any = None) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    if isinstance(instructions, str) and instructions.strip():
        messages.append(ChatMessage(role="developer", content=instructions.strip()))
    elif isinstance(instructions, list) and instructions:
        text = "\n".join(str(item) for item in instructions if str(item).strip())
        if text:
            messages.append(ChatMessage(role="developer", content=text))

    if isinstance(input_value, str):
        messages.append(ChatMessage(role="user", content=input_value))
        return messages

    if not isinstance(input_value, list):
        raise HTTPException(status_code=400, detail="Responses input must be a string or an input item array")

    loose_parts: list[Any] = []
    for item in input_value:
        if isinstance(item, str):
            loose_parts.append({"type": "input_text", "text": item})
            continue
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "")
        if item_type == "message" or item.get("role"):
            role = str(item.get("role") or "user")
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                role = "user"
            content = item.get("content", "")
            if isinstance(content, list):
                content = [_content_part(part) for part in content]
            messages.append(ChatMessage(role=role, content=content))
        elif item_type in {"input_text", "input_image", "input_file"}:
            loose_parts.append(_content_part(item))
        elif item_type in {"function_call_output", "computer_call_output", "custom_tool_call_output"}:
            raise HTTPException(status_code=400, detail=f"Responses item type {item_type!r} is not supported by the browser bridge")

    if loose_parts:
        messages.append(ChatMessage(role="user", content=loose_parts))
    if not any(message.role == "user" and message.text() for message in messages):
        raise HTTPException(status_code=400, detail="Responses input must contain a non-empty user message")
    return messages


class ResponsesRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = DEFAULT_TEXT_MODEL
    input: Any
    instructions: Any = None
    stream: bool = False
    reasoning: dict[str, Any] | None = None
    reasoning_effort: str | None = None
    client_id: str | None = None
    timeout: int | None = Field(default=None, ge=5, le=900)
    max_output_tokens: int | None = Field(default=None, ge=1)
    tools: list[Any] | None = None


class LegacyCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = DEFAULT_TEXT_MODEL
    prompt: Any
    stream: bool = False
    reasoning_effort: str | None = None
    reasoning: dict[str, Any] | None = None
    client_id: str | None = None
    timeout: int | None = Field(default=None, ge=5, le=900)
    max_tokens: int | None = Field(default=None, ge=1)


def _legacy_prompt(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if not value:
            return ""
        if all(isinstance(item, str) for item in value):
            return "\n".join(value)
    return str(value or "")


def _ignored_parameters(body: BaseModel, supported: set[str]) -> list[str]:
    extra = body.model_extra or {}
    ignored = []
    for key, value in extra.items():
        if key not in supported and value not in (None, False, [], {}, ""):
            ignored.append(key)
    for key in ("max_output_tokens", "max_tokens"):
        if hasattr(body, key) and getattr(body, key, None) is not None:
            ignored.append(key)
    return sorted(set(ignored))


def install_v13_patch(app: FastAPI) -> FastAPI:
    settings = app.state.settings
    registry = app.state.registry
    api_keys = app.state.api_keys
    app.version = PATCH_VERSION

    chat_route = next(
        route for route in app.router.routes
        if str(getattr(route, "path", "")) == "/v1/chat/completions" and "POST" in set(getattr(route, "methods", set()) or set())
    )
    chat_endpoint = chat_route.endpoint

    base_send = registry.send
    if not getattr(registry, "_chat2api_v13_send_wrapped", False):
        async def send_with_model_target(client_id: str, payload: dict[str, Any]):
            target = _target_context.get()
            if target and payload.get("type") == "chat.request":
                options = dict(payload.get("options") or {})
                options["model"] = target["model"]
                if target.get("reasoning_level"):
                    options["reasoning_level"] = target["reasoning_level"]
                    options["reasoning_effort"] = target["reasoning_effort"]
                payload = {**payload, "options": options}
            return await base_send(client_id, payload)

        registry.send = send_with_model_target
        registry._chat2api_v13_send_wrapped = True

    base_model_catalog = registry.model_catalog

    def model_catalog_v13(online_only: bool = True) -> list[dict[str, Any]]:
        base_rows = list(base_model_catalog(online_only=online_only))
        by_id = {str(row.get("id") or ""): dict(row) for row in base_rows if isinstance(row, dict)}
        clients = registry.online_client_ids() if online_only else sorted(registry.clients)
        rows: list[dict[str, Any]] = []
        for model_id, label in (("gpt-5.6-sol", "GPT-5.6 Sol"), ("gpt-5.5", "GPT-5.5")):
            existing = by_id.get(model_id, {})
            rows.append({
                "id": model_id,
                "object": "model",
                "created": 0,
                "owned_by": "chat2api",
                "label": label,
                "capabilities": ["text", "vision", "file-understanding"],
                "reasoning_efforts": ["low", "medium", "high"],
                "reasoning_labels": {"low": "极速", "medium": "中", "high": "高"},
                "clients": list(existing.get("clients") or clients),
            })
        for model_id in SPECIAL_MODELS:
            if model_id in by_id:
                rows.append(by_id[model_id])
        return rows

    registry.model_catalog = model_catalog_v13

    def supplied_token(request: Request) -> str:
        supplied = (request.headers.get("x-api-key") or "").strip()
        authorization = request.headers.get("authorization") or ""
        if authorization.lower().startswith("bearer "):
            supplied = authorization[7:].strip()
        return supplied

    async def require_chat_principal(request: Request) -> ApiPrincipal:
        supplied = supplied_token(request)
        if not supplied:
            raise HTTPException(status_code=401, detail="Missing API key")
        if settings.api_key and secrets.compare_digest(supplied, settings.api_key):
            return ApiPrincipal(
                key_id="master", name="CHAT2API_API_KEY", kind="master",
                scopes=("admin", "chat", "models", "files", "images", "audio"),
            )
        principal = await api_keys.authenticate(supplied)
        if not principal:
            raise HTTPException(status_code=401, detail="Invalid or disabled API key")
        if "chat" not in principal.scopes:
            raise HTTPException(status_code=403, detail="API key does not have 'chat' permission")
        return principal

    async def response_bytes(response) -> bytes:
        body = getattr(response, "body", None)
        if body is not None:
            return bytes(body)
        chunks: list[bytes] = []
        iterator = getattr(response, "body_iterator", None)
        if iterator is not None:
            async for chunk in iterator:
                chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
        return b"".join(chunks)

    def response_object(*, response_id: str, model: str, text: str, usage: dict[str, Any] | None, meta: dict[str, Any] | None, reasoning_effort: str | None, ignored: list[str]) -> dict[str, Any]:
        item_id = "msg_" + uuid.uuid4().hex
        return {
            "id": response_id,
            "object": "response",
            "created_at": int(time.time()),
            "status": "completed",
            "error": None,
            "incomplete_details": None,
            "instructions": None,
            "model": model,
            "output": [{
                "id": item_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }],
            "output_text": text,
            "reasoning": {"effort": reasoning_effort, "summary": None},
            "usage": _usage_for_responses(usage),
            "chat2api": {
                **(meta or {}),
                "compatibility": {
                    "api": "responses",
                    "ignored_parameters": ignored,
                    "reasoning_effort": reasoning_effort,
                },
            },
        }

    async def iter_chat_sse(response) -> AsyncIterator[dict[str, Any]]:
        buffer = ""
        async for chunk in response.body_iterator:
            buffer += chunk.decode("utf-8", errors="replace") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
            while "\n\n" in buffer:
                block, buffer = buffer.split("\n\n", 1)
                for line in block.splitlines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        yield json.loads(data)
                    except json.JSONDecodeError:
                        continue

    @app.get("/v1/models/{model_id}")
    async def retrieve_model(model_id: str, request: Request) -> dict[str, Any]:
        await require_chat_principal(request)
        catalog = {row["id"]: row for row in registry.model_catalog(online_only=True)}
        if model_id not in catalog:
            raise HTTPException(status_code=404, detail="Model not found")
        return catalog[model_id]

    @app.post("/v1/responses")
    async def responses(body: ResponsesRequest, request: Request):
        principal = await require_chat_principal(request)
        target = _target_from_payload(body.model_dump(exclude_none=True))
        if body.tools:
            raise HTTPException(status_code=400, detail="Responses tools are not supported by the ChatGPT browser bridge")
        messages = _responses_messages(body.input, body.instructions)
        chat_body = ChatCompletionRequest(
            model=target["model"],
            messages=messages,
            stream=body.stream,
            client_id=body.client_id,
            prompt_mode="full",
            timeout=body.timeout,
            reasoning_effort=target.get("reasoning_effort"),
        )
        chat_response = await chat_endpoint(
            body=chat_body,
            request=request,
            x_chat2api_client=request.headers.get("x-chat2api-client"),
            x_chat2api_request_id=request.headers.get("x-chat2api-request-id"),
            principal=principal,
        )
        ignored = _ignored_parameters(body, {"metadata", "store", "previous_response_id", "temperature", "top_p"})
        response_id = "resp_" + uuid.uuid4().hex

        if not body.stream:
            raw = await response_bytes(chat_response)
            payload = json.loads(raw.decode("utf-8"))
            text = str(payload.get("choices", [{}])[0].get("message", {}).get("content") or "")
            return JSONResponse(response_object(
                response_id=response_id,
                model=target["model"],
                text=text,
                usage=payload.get("usage"),
                meta=payload.get("chat2api"),
                reasoning_effort=target.get("reasoning_effort"),
                ignored=ignored,
            ))

        async def stream_responses() -> AsyncIterator[str]:
            item_id = "msg_" + uuid.uuid4().hex
            sequence = 0
            text = ""
            usage: dict[str, Any] | None = None
            meta: dict[str, Any] | None = None

            def event(payload: dict[str, Any]) -> str:
                nonlocal sequence
                sequence += 1
                payload.setdefault("sequence_number", sequence)
                return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"

            created = response_object(
                response_id=response_id, model=target["model"], text="", usage=None, meta=None,
                reasoning_effort=target.get("reasoning_effort"), ignored=ignored,
            )
            created["status"] = "in_progress"
            created["output"] = []
            created["output_text"] = ""
            created["usage"] = None
            yield event({"type": "response.created", "response": created})
            yield event({
                "type": "response.output_item.added", "output_index": 0,
                "item": {"id": item_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []},
            })
            yield event({
                "type": "response.content_part.added", "item_id": item_id, "output_index": 0, "content_index": 0,
                "part": {"type": "output_text", "text": "", "annotations": []},
            })

            async for payload in iter_chat_sse(chat_response):
                if payload.get("error"):
                    yield event({"type": "response.failed", "response": {"id": response_id, "object": "response", "status": "failed", "error": payload["error"], "model": target["model"]}})
                    return
                delta = str(payload.get("choices", [{}])[0].get("delta", {}).get("content") or "")
                if delta:
                    text += delta
                    yield event({
                        "type": "response.output_text.delta", "item_id": item_id,
                        "output_index": 0, "content_index": 0, "delta": delta,
                    })
                if payload.get("usage"):
                    usage = payload.get("usage")
                if payload.get("chat2api"):
                    meta = payload.get("chat2api")

            yield event({"type": "response.output_text.done", "item_id": item_id, "output_index": 0, "content_index": 0, "text": text})
            yield event({
                "type": "response.content_part.done", "item_id": item_id, "output_index": 0, "content_index": 0,
                "part": {"type": "output_text", "text": text, "annotations": []},
            })
            yield event({
                "type": "response.output_item.done", "output_index": 0,
                "item": {"id": item_id, "type": "message", "status": "completed", "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": []}]},
            })
            yield event({"type": "response.completed", "response": response_object(
                response_id=response_id, model=target["model"], text=text, usage=usage, meta=meta,
                reasoning_effort=target.get("reasoning_effort"), ignored=ignored,
            )})

        return StreamingResponse(
            stream_responses(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/completions")
    async def legacy_completions(body: LegacyCompletionRequest, request: Request):
        principal = await require_chat_principal(request)
        target = _target_from_payload(body.model_dump(exclude_none=True))
        prompt = _legacy_prompt(body.prompt).strip()
        if not prompt:
            raise HTTPException(status_code=400, detail="prompt must not be empty")
        chat_body = ChatCompletionRequest(
            model=target["model"],
            messages=[ChatMessage(role="user", content=prompt)],
            stream=body.stream,
            client_id=body.client_id,
            timeout=body.timeout,
            reasoning_effort=target.get("reasoning_effort"),
        )
        chat_response = await chat_endpoint(
            body=chat_body,
            request=request,
            x_chat2api_client=request.headers.get("x-chat2api-client"),
            x_chat2api_request_id=request.headers.get("x-chat2api-request-id"),
            principal=principal,
        )
        completion_id = "cmpl_" + uuid.uuid4().hex
        if not body.stream:
            raw = await response_bytes(chat_response)
            payload = json.loads(raw.decode("utf-8"))
            text = str(payload.get("choices", [{}])[0].get("message", {}).get("content") or "")
            return JSONResponse({
                "id": completion_id,
                "object": "text_completion",
                "created": int(time.time()),
                "model": target["model"],
                "choices": [{"text": text, "index": 0, "logprobs": None, "finish_reason": "stop"}],
                "usage": payload.get("usage") or {},
                "chat2api": payload.get("chat2api") or {},
            })

        async def stream_legacy() -> AsyncIterator[str]:
            async for payload in iter_chat_sse(chat_response):
                if payload.get("error"):
                    yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                    continue
                delta = str(payload.get("choices", [{}])[0].get("delta", {}).get("content") or "")
                finish = payload.get("choices", [{}])[0].get("finish_reason")
                if delta or finish:
                    chunk = {
                        "id": completion_id,
                        "object": "text_completion",
                        "created": int(time.time()),
                        "model": target["model"],
                        "choices": [{"text": delta, "index": 0, "logprobs": None, "finish_reason": finish}],
                    }
                    if payload.get("usage"):
                        chunk["usage"] = payload["usage"]
                    if payload.get("chat2api"):
                        chunk["chat2api"] = payload["chat2api"]
                    yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            stream_legacy(), media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/assets/chat2api-v13.js")
    async def admin_v13_js() -> Response:
        path = Path(__file__).with_name("admin_v13.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def v13_model_target_and_console(request: Request, call_next):
        target = None
        if request.method == "POST" and request.url.path in {"/v1/chat/completions", "/v1/responses", "/v1/completions"}:
            try:
                payload = await request.json()
                if isinstance(payload, dict):
                    target = _target_from_payload(payload)
            except ValueError as error:
                return _openai_error(str(error), param="model/reasoning_effort")
            except Exception:
                target = None
        token = _target_context.set(target)
        try:
            response = await call_next(request)
        finally:
            _target_context.reset(token)

        path = request.url.path
        content_type = response.headers.get("content-type", "")
        if path in {"/", "/healthz", "/api/admin/overview"} and "application/json" in content_type:
            raw = await response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            if isinstance(payload, dict):
                payload["version"] = PATCH_VERSION
                if path == "/api/admin/overview":
                    payload["models"] = registry.model_catalog(online_only=True)
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["openai_responses_api"] = True
                        capabilities["openai_legacy_completions"] = True
                        capabilities["canonical_text_models"] = list(TEXT_MODELS)
                        capabilities["reasoning_efforts"] = ["low", "medium", "high"]
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v13.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
