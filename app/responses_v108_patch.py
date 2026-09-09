from __future__ import annotations

import asyncio
import json
import secrets
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .api_keys import ApiPrincipal
from .token_usage import usage_for


PATCH_REVISION = 108
SUPPORTED_NATIVE_TOOLS = {"web_search", "web_search_preview"}


def _error(message: str, *, status_code: int = 400, param: str | None = None, code: str | None = None) -> JSONResponse:
    return JSONResponse(
        {"error": {"message": message, "type": "invalid_request_error" if status_code < 500 else "server_error", "param": param, "code": code}},
        status_code=status_code,
    )


def _supplied_token(request: Request) -> str:
    authorization = str(request.headers.get("authorization") or "").strip()
    supplied = str(request.headers.get("x-api-key") or "").strip()
    if authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    return supplied


async def _authenticate(app: FastAPI, request: Request) -> ApiPrincipal:
    supplied = _supplied_token(request)
    if not supplied:
        raise HTTPException(status_code=401, detail="Missing API key")
    config = app.state.settings
    if config.api_key and secrets.compare_digest(supplied, config.api_key):
        return ApiPrincipal(key_id="master", name="CHAT2API_API_KEY", kind="master", scopes=("admin", "chat", "models", "files", "images"))
    principal = await app.state.api_keys.authenticate(supplied)
    if not principal:
        raise HTTPException(status_code=401, detail="Invalid or disabled API key")
    if "chat" not in principal.scopes and "admin" not in principal.scopes:
        raise HTTPException(status_code=403, detail="API key does not have chat scope")
    return principal


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for item in content:
        if isinstance(item, str):
            chunks.append(item)
        elif isinstance(item, dict) and str(item.get("type") or "") in {"input_text", "output_text", "text"}:
            chunks.append(str(item.get("text") or ""))
    return "".join(chunks)


def _input_prompt(body: dict[str, Any]) -> str:
    sections: list[str] = []
    instructions = str(body.get("instructions") or "").strip()
    if instructions:
        sections.append(f"Developer instructions:\n{instructions}")
    value = body.get("input", "")
    if isinstance(value, str):
        if value.strip():
            sections.append(value.strip())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    sections.append(item.strip())
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "message")
            if kind in {"message", "input_message", "easy_input_message"}:
                text = _content_text(item.get("content"))
                if text.strip():
                    sections.append(f"{str(item.get('role') or 'user')}: {text.strip()}")
            elif kind == "function_call_output":
                output = item.get("output")
                if isinstance(output, (dict, list)):
                    output = json.dumps(output, ensure_ascii=False)
                sections.append(f"Tool output for {str(item.get('call_id') or '')}:\n{str(output or '')}")
            elif kind == "function_call":
                sections.append("Previous function call: " + json.dumps({"call_id": item.get("call_id"), "name": item.get("name"), "arguments": item.get("arguments")}, ensure_ascii=False))
    prompt = "\n\n".join(part for part in sections if part.strip()).strip()
    if not prompt:
        raise ValueError("input must contain text")
    return prompt


def _tool_config(body: dict[str, Any]) -> tuple[list[str], bool]:
    raw = body.get("tools") or []
    if not isinstance(raw, list):
        raise ValueError("tools must be an array")
    native: list[str] = []
    unsupported: list[str] = []
    for tool in raw:
        if not isinstance(tool, dict):
            raise ValueError("each tool must be an object")
        kind = str(tool.get("type") or "").strip()
        if not kind:
            raise ValueError("tool.type is required")
        if kind in SUPPORTED_NATIVE_TOOLS:
            native.append("web_search")
        else:
            unsupported.append(kind)
    if unsupported:
        raise NotImplementedError(
            "Responses v108 currently supports native ChatGPT Web Search only; external tool types are reserved for the emulated tool bridge phase: "
            + ", ".join(sorted(set(unsupported)))
        )
    choice = body.get("tool_choice", "auto")
    force = choice == "required" or (isinstance(choice, dict) and str(choice.get("type") or "") in SUPPORTED_NATIVE_TOOLS)
    return list(dict.fromkeys(native)), force


def _decorate_prompt(prompt: str, native_tools: list[str], force: bool) -> str:
    if "web_search" not in native_tools:
        return prompt
    policy = "You MUST use ChatGPT's native web search tool before answering this request." if force else "ChatGPT native web search is available for this response. Use it when it is necessary to answer accurately."
    return f"{policy}\n\n{prompt}"


def _query(arguments: str) -> str:
    text = str(arguments or "").strip()
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and parts[0].strip().lower() in {"fast", "slow", "search", "image"}:
            return parts[1].strip()
    if text.startswith("search("):
        return text[7:].strip().strip("()").strip('"')
    return text[:4000]


def _sources(metadata: dict[str, Any]) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    groups = metadata.get("search_result_groups") if isinstance(metadata, dict) else None
    if not isinstance(groups, list):
        return output
    for group in groups:
        entries = group.get("entries") if isinstance(group, dict) else None
        if not isinstance(entries, list):
            continue
        for item in entries:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            output.append({"type": "url", "url": str(item["url"]), "title": str(item.get("title") or "")})
            if len(output) >= 50:
                return output
    return output


def _usage(prompt: str, text: str) -> dict[str, Any]:
    value = usage_for(prompt, text)
    return {
        "input_tokens": value.prompt_tokens,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": value.completion_tokens,
        "output_tokens_details": {"reasoning_tokens": 0},
        "total_tokens": value.total_tokens,
    }


def _web_item(call: dict[str, Any], include_sources: bool) -> dict[str, Any]:
    action: dict[str, Any] = {"type": "search", "query": _query(str(call.get("arguments") or ""))}
    if include_sources and call.get("sources"):
        action["sources"] = call["sources"]
    return {"id": str(call["item_id"]), "type": "web_search_call", "status": str(call.get("status") or "completed"), "action": action}


def _message_item(message_id: str, text: str, status: str = "completed") -> dict[str, Any]:
    return {"id": message_id, "type": "message", "status": status, "role": "assistant", "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}


def _response(response_id: str, body: dict[str, Any], prompt: str, text: str, output: list[dict[str, Any]], created_at: int, status: str = "completed") -> dict[str, Any]:
    return {
        "id": response_id,
        "object": "response",
        "created_at": created_at,
        "completed_at": int(time.time()) if status == "completed" else None,
        "status": status,
        "error": None,
        "incomplete_details": None,
        "instructions": body.get("instructions"),
        "max_output_tokens": body.get("max_output_tokens"),
        "model": str(body.get("model") or "gpt-5.6-sol"),
        "output": output,
        "parallel_tool_calls": bool(body.get("parallel_tool_calls", True)),
        "previous_response_id": body.get("previous_response_id"),
        "reasoning": body.get("reasoning") or {"effort": None, "summary": None},
        "store": bool(body.get("store", True)),
        "temperature": body.get("temperature"),
        "text": body.get("text") or {"format": {"type": "text"}},
        "tool_choice": body.get("tool_choice", "auto"),
        "tools": body.get("tools") or [],
        "top_p": body.get("top_p"),
        "truncation": body.get("truncation", "disabled"),
        "usage": _usage(prompt, text),
        "metadata": body.get("metadata") or {},
        "output_text": text,
    }


def _sse(event_type: str, sequence: int, **payload: Any) -> str:
    data = {"type": event_type, "sequence_number": sequence, **payload}
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _dispatch(app: FastAPI, request: Request, body: dict[str, Any], principal: ApiPrincipal, prompt: str, native_tools: list[str]):
    registry = app.state.registry
    broker = app.state.broker
    requested = str(body.get("client_id") or request.headers.get("x-chat2api-client") or "").strip() or None
    registry.set_routing_key(principal.key_id)
    try:
        client_id = registry.resolve_client(requested)
    except (KeyError, ConnectionError) as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    raw_id = str(request.headers.get("x-chat2api-request-id") or "").strip()
    request_id = raw_id if raw_id and len(raw_id) <= 128 else "resp_req_" + uuid.uuid4().hex
    try:
        state = await broker.create(request_id, client_id)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    timeout = max(5, min(int(body.get("timeout") or app.state.settings.request_timeout_seconds), 3600))
    registry.busy_clients.add(client_id)
    try:
        await registry.send(client_id, {
            "type": "chat.request",
            "request_id": request_id,
            "prompt": prompt,
            "attachments": [],
            "options": {
                "auto_switch_text": True,
                "timeout_seconds": timeout,
                "model": str(body.get("model") or "gpt-5.6-sol"),
                "response_protocol": "responses-v108",
                "native_tools": native_tools,
            },
        })
    except Exception:
        registry.busy_clients.discard(client_id)
        await broker.release(request_id)
        raise
    return state, request_id, client_id, timeout


def _new_call(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "native_id": str(event.get("call_id") or uuid.uuid4().hex),
        "item_id": "ws_" + uuid.uuid4().hex,
        "arguments": str(event.get("arguments") or ""),
        "status": "in_progress",
        "sources": [],
    }


async def _collect(state: Any, timeout: int, include_sources: bool) -> tuple[str, list[dict[str, Any]]]:
    deadline = asyncio.get_running_loop().time() + timeout
    text = ""
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        try:
            event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
        except asyncio.TimeoutError:
            continue
        kind = str(event.get("type") or "")
        if kind == "chat.tool.call" and str(event.get("tool_name") or "") == "web.run":
            call = _new_call(event)
            calls.append(call)
            by_id[call["native_id"]] = call
        elif kind == "chat.tool.result" and str(event.get("tool_name") or "") == "web.run":
            call = by_id.get(str(event.get("call_id") or "")) or (calls[-1] if calls else None)
            if call:
                found = _sources(event.get("metadata") if isinstance(event.get("metadata"), dict) else {})
                if found:
                    call["sources"] = found
                call["status"] = "completed"
        elif kind == "chat.response.snapshot":
            text = str(event.get("text") or text)
        elif kind == "chat.response.completed":
            text = str(event.get("text") or text)
            if text:
                break
        elif kind == "chat.completed" and not text:
            text = str(event.get("text") or "")
            if text:
                break
        elif kind in {"chat.error", "chat.cancelled"}:
            raise RuntimeError(str(event.get("error") or event.get("reason") or "Browser request failed"))
    for call in calls:
        call["status"] = "completed"
    return text, [_web_item(call, include_sources) for call in calls]


async def _stream(app: FastAPI, request: Request, state: Any, *, response_id: str, body: dict[str, Any], prompt: str, request_id: str, client_id: str, timeout: int, created_at: int, include_sources: bool) -> AsyncIterator[str]:
    seq = 1
    message_id = "msg_" + uuid.uuid4().hex
    calls: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    text = ""
    sent = ""
    message_added = False
    deadline = asyncio.get_running_loop().time() + timeout
    created = _response(response_id, body, prompt, "", [], created_at, status="in_progress")
    created["usage"] = None
    yield _sse("response.created", seq, response=created)
    try:
        while True:
            if await request.is_disconnected():
                try:
                    await app.state.registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
                except Exception:
                    pass
                return
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                event = await asyncio.wait_for(state.queue.get(), timeout=min(1.0, remaining))
            except asyncio.TimeoutError:
                continue
            kind = str(event.get("type") or "")
            if kind == "chat.tool.call" and str(event.get("tool_name") or "") == "web.run":
                call = _new_call(event)
                call["output_index"] = len(calls)
                calls.append(call)
                by_id[call["native_id"]] = call
                seq += 1
                yield _sse("response.output_item.added", seq, output_index=call["output_index"], item=_web_item(call, include_sources))
                continue
            if kind == "chat.tool.result" and str(event.get("tool_name") or "") == "web.run":
                call = by_id.get(str(event.get("call_id") or "")) or (calls[-1] if calls else None)
                if call and call.get("status") != "completed":
                    found = _sources(event.get("metadata") if isinstance(event.get("metadata"), dict) else {})
                    if found:
                        call["sources"] = found
                    call["status"] = "completed"
                    seq += 1
                    yield _sse("response.output_item.done", seq, output_index=call["output_index"], item=_web_item(call, include_sources))
                continue
            if kind == "chat.response.snapshot":
                snapshot = str(event.get("text") or "")
                if not snapshot:
                    continue
                text = snapshot
                if not message_added:
                    message_added = True
                    seq += 1
                    yield _sse("response.output_item.added", seq, output_index=len(calls), item=_message_item(message_id, "", "in_progress"))
                    seq += 1
                    yield _sse("response.content_part.added", seq, item_id=message_id, output_index=len(calls), content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []})
                if snapshot.startswith(sent):
                    delta = snapshot[len(sent):]
                    if delta:
                        sent = snapshot
                        seq += 1
                        yield _sse("response.output_text.delta", seq, item_id=message_id, output_index=len(calls), content_index=0, delta=delta, logprobs=[])
                continue
            if kind == "chat.response.completed":
                text = str(event.get("text") or text)
                if text:
                    break
            elif kind == "chat.completed" and not text:
                text = str(event.get("text") or "")
                if text:
                    break
            elif kind in {"chat.error", "chat.cancelled"}:
                raise RuntimeError(str(event.get("error") or event.get("reason") or "Browser request failed"))

        for call in calls:
            if call.get("status") != "completed":
                call["status"] = "completed"
                seq += 1
                yield _sse("response.output_item.done", seq, output_index=call["output_index"], item=_web_item(call, include_sources))
        if not message_added:
            message_added = True
            seq += 1
            yield _sse("response.output_item.added", seq, output_index=len(calls), item=_message_item(message_id, "", "in_progress"))
            seq += 1
            yield _sse("response.content_part.added", seq, item_id=message_id, output_index=len(calls), content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []})
        if text.startswith(sent) and text[len(sent):]:
            seq += 1
            yield _sse("response.output_text.delta", seq, item_id=message_id, output_index=len(calls), content_index=0, delta=text[len(sent):], logprobs=[])
        seq += 1
        yield _sse("response.output_text.done", seq, item_id=message_id, output_index=len(calls), content_index=0, text=text, logprobs=[])
        seq += 1
        yield _sse("response.content_part.done", seq, item_id=message_id, output_index=len(calls), content_index=0, part={"type": "output_text", "text": text, "annotations": [], "logprobs": []})
        seq += 1
        yield _sse("response.output_item.done", seq, output_index=len(calls), item=_message_item(message_id, text))
        output = [_web_item(call, include_sources) for call in calls] + [_message_item(message_id, text)]
        completed = _response(response_id, body, prompt, text, output, created_at)
        if bool(body.get("store", True)):
            store: OrderedDict[str, dict[str, Any]] = app.state.responses_v108_store
            store[response_id] = completed
            while len(store) > 256:
                store.popitem(last=False)
        seq += 1
        yield _sse("response.completed", seq, response=completed)
    except asyncio.TimeoutError:
        seq += 1
        failed = _response(response_id, body, prompt, text, [], created_at, status="failed")
        failed["error"] = {"code": "timeout", "message": "Timed out waiting for ChatGPT"}
        yield _sse("response.failed", seq, response=failed)
    except Exception as error:
        seq += 1
        failed = _response(response_id, body, prompt, text, [], created_at, status="failed")
        failed["error"] = {"code": "browser_error", "message": str(error)}
        yield _sse("response.failed", seq, response=failed)
    finally:
        app.state.registry.busy_clients.discard(client_id)
        await app.state.broker.release(request_id)


def install_responses_v108_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_v108_installed", False):
        return app
    app.state.responses_v108_installed = True
    app.state.responses_v108_store = OrderedDict()

    @app.post("/v1/responses")
    async def create_response(request: Request):
        principal = await _authenticate(app, request)
        try:
            body = await request.json()
        except Exception:
            return _error("Request body must be valid JSON")
        if not isinstance(body, dict):
            return _error("Request body must be a JSON object")
        try:
            prompt = _input_prompt(body)
            native_tools, force = _tool_config(body)
        except ValueError as error:
            return _error(str(error), param="input")
        except NotImplementedError as error:
            return _error(str(error), param="tools", code="responses_external_tools_pending")
        prompt = _decorate_prompt(prompt, native_tools, force)
        response_id = "resp_" + uuid.uuid4().hex
        created_at = int(time.time())
        include = body.get("include") if isinstance(body.get("include"), list) else []
        include_sources = "web_search_call.action.sources" in include
        try:
            state, request_id, client_id, timeout = await _dispatch(app, request, body, principal, prompt, native_tools)
        except HTTPException:
            raise
        except Exception as error:
            return _error(str(error), status_code=503, code="browser_dispatch_failed")
        if bool(body.get("stream", False)):
            return StreamingResponse(
                _stream(app, request, state, response_id=response_id, body=body, prompt=prompt, request_id=request_id, client_id=client_id, timeout=timeout, created_at=created_at, include_sources=include_sources),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        try:
            text, web_items = await _collect(state, timeout, include_sources)
            output = web_items + [_message_item("msg_" + uuid.uuid4().hex, text)]
            response = _response(response_id, body, prompt, text, output, created_at)
            if bool(body.get("store", True)):
                store: OrderedDict[str, dict[str, Any]] = app.state.responses_v108_store
                store[response_id] = response
                while len(store) > 256:
                    store.popitem(last=False)
            return JSONResponse(response)
        except asyncio.TimeoutError:
            try:
                await app.state.registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
            except Exception:
                pass
            return _error("Timed out waiting for ChatGPT", status_code=504, code="timeout")
        except RuntimeError as error:
            return _error(str(error), status_code=502, code="browser_error")
        finally:
            app.state.registry.busy_clients.discard(client_id)
            await app.state.broker.release(request_id)

    @app.get("/v1/responses/{response_id}")
    async def retrieve_response(response_id: str, request: Request):
        await _authenticate(app, request)
        value = app.state.responses_v108_store.get(str(response_id))
        if not value:
            return _error("Response not found", status_code=404, code="response_not_found")
        return JSONResponse(value)

    return app
