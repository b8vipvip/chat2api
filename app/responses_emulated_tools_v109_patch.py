from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .responses_v108_patch import (
    SUPPORTED_NATIVE_TOOLS,
    _authenticate,
    _collect,
    _dispatch,
    _error,
    _message_item,
    _response,
    _sse,
    _usage,
)


PATCH_REVISION = 109
BRIDGE_START = "<<<CHAT2API_RESPONSES_TOOL_V109>>>"
BRIDGE_END = "<<<END_CHAT2API_RESPONSES_TOOL_V109>>>"
SUPPORTED_EMULATED_TYPES = {"function", "namespace", "custom", "tool_search"}


def _request_tools(body: dict[str, Any]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    raw = body.get("tools")
    if isinstance(raw, list):
        tools.extend(item for item in raw if isinstance(item, dict))
    value = body.get("input")
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "")
            if kind not in {"additional_tools", "tool_search_output"}:
                continue
            nested = item.get("tools")
            if isinstance(nested, list):
                tools.extend(tool for tool in nested if isinstance(tool, dict))
    return tools


def needs_emulated_tools(body: dict[str, Any]) -> bool:
    for tool in _request_tools(body):
        kind = str(tool.get("type") or "").strip()
        if kind and kind not in SUPPORTED_NATIVE_TOOLS:
            return True
    return False


def _catalog(body: dict[str, Any]) -> list[dict[str, Any]]:
    catalog: list[dict[str, Any]] = []
    for tool in _request_tools(body):
        kind = str(tool.get("type") or "").strip()
        if kind in SUPPORTED_NATIVE_TOOLS:
            continue
        if kind == "namespace":
            namespace = str(tool.get("name") or "").strip()
            nested = tool.get("tools")
            if not namespace or not isinstance(nested, list):
                continue
            for child in nested:
                if not isinstance(child, dict):
                    continue
                child_kind = str(child.get("type") or "function").strip()
                if child_kind not in {"function", "custom"}:
                    continue
                name = str(child.get("name") or "").strip()
                if not name:
                    continue
                catalog.append(
                    {
                        "type": child_kind,
                        "namespace": namespace,
                        "name": name,
                        "description": str(child.get("description") or ""),
                        "parameters": child.get("parameters") if isinstance(child.get("parameters"), dict) else {},
                        "format": child.get("format"),
                    }
                )
            continue
        if kind in {"function", "custom", "tool_search"}:
            name = str(tool.get("name") or ("tool_search" if kind == "tool_search" else "")).strip()
            if not name:
                continue
            catalog.append(
                {
                    "type": kind,
                    "namespace": str(tool.get("namespace") or "").strip() or None,
                    "name": name,
                    "description": str(tool.get("description") or ""),
                    "parameters": tool.get("parameters") if isinstance(tool.get("parameters"), dict) else {},
                    "format": tool.get("format"),
                    "execution": tool.get("execution"),
                }
            )
    return catalog


def _output_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value or "")


def _input_context(body: dict[str, Any]) -> str:
    parts: list[str] = []
    instructions = str(body.get("instructions") or "").strip()
    if instructions:
        parts.append("DEVELOPER INSTRUCTIONS:\n" + instructions)
    value = body.get("input")
    if isinstance(value, str):
        if value.strip():
            parts.append("USER INPUT:\n" + value.strip())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    parts.append("INPUT:\n" + item.strip())
                continue
            if not isinstance(item, dict):
                continue
            kind = str(item.get("type") or "message")
            if kind == "additional_tools":
                continue
            if kind in {"message", "input_message", "easy_input_message"}:
                content = item.get("content")
                texts: list[str] = []
                if isinstance(content, str):
                    texts.append(content)
                elif isinstance(content, list):
                    for piece in content:
                        if isinstance(piece, str):
                            texts.append(piece)
                        elif isinstance(piece, dict):
                            text = piece.get("text") or piece.get("content")
                            if isinstance(text, str):
                                texts.append(text)
                text = "".join(texts).strip()
                if text:
                    parts.append(f"{str(item.get('role') or 'user').upper()} MESSAGE:\n{text}")
                continue
            if kind in {"function_call", "custom_tool_call", "tool_search_call"}:
                parts.append("PRIOR TOOL CALL:\n" + json.dumps(item, ensure_ascii=False, separators=(",", ":")))
                continue
            if kind in {"function_call_output", "custom_tool_call_output", "mcp_tool_call_output"}:
                parts.append(
                    f"TOOL RESULT ({str(item.get('call_id') or '')}):\n{_output_text(item.get('output'))}"
                )
                continue
            if kind == "tool_search_output":
                parts.append(
                    f"TOOL SEARCH RESULT ({str(item.get('call_id') or '')}):\n{_output_text(item.get('tools'))}"
                )
                continue
            parts.append("PRIOR RESPONSE ITEM:\n" + json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    return "\n\n".join(parts).strip()


def _stored_context(app: FastAPI, body: dict[str, Any]) -> str:
    previous = str(body.get("previous_response_id") or "").strip()
    if not previous:
        return ""
    store = getattr(app.state, "responses_v109_context_store", None)
    if not isinstance(store, OrderedDict):
        return ""
    response = store.get(previous)
    if not isinstance(response, dict):
        return ""
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    return "PREVIOUS RESPONSE OUTPUT:\n" + json.dumps(output, ensure_ascii=False, separators=(",", ":"))


def _bridge_prompt(app: FastAPI, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    catalog = _catalog(body)
    if not catalog:
        raise ValueError("No emulatable Responses tools were supplied")
    context = _input_context(body)
    previous = _stored_context(app, body)
    choice = body.get("tool_choice", "auto")
    policy = {
        "bridge": "chat2api-responses-tool-v109",
        "tool_choice": choice,
        "parallel_tool_calls": bool(body.get("parallel_tool_calls", True)),
        "tools": catalog,
    }
    protocol = f"""You are operating behind a strict OpenAI Responses tool-call transport adapter.
Do not pretend that you executed any listed tool. The caller, not you, executes tools.
Choose tools only from the supplied catalog and preserve namespace/name exactly.

Return exactly one JSON object between these two sentinel lines:
{BRIDGE_START}
{{"kind":"tool_calls","calls":[{{"namespace":null,"name":"tool_name","arguments":{{}}}}]}}
{BRIDGE_END}

Or, only when no further tool execution is needed:
{BRIDGE_START}
{{"kind":"final","text":"final answer"}}
{BRIDGE_END}

Rules:
- For function tools, arguments must be a JSON object matching the declared parameters.
- For custom tools, use {{"name":"...","input":"raw custom tool input"}}.
- For namespace tools, include the exact namespace.
- For tool_search, use {{"name":"...","arguments":{{...}}}}.
- Never put tool output, explanations, Markdown fences, or any text outside the sentinel block.
- If tool_choice requires a tool, you must return kind=tool_calls.
- After TOOL RESULT items appear in the conversation context, use them as observations and either request the next tool or return kind=final.
"""
    chunks = [protocol, "TOOL CATALOG:\n" + json.dumps(policy, ensure_ascii=False, separators=(",", ":"))]
    if previous:
        chunks.append(previous)
    if context:
        chunks.append("CONVERSATION CONTEXT:\n" + context)
    return "\n\n".join(chunks), catalog


def _json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if BRIDGE_START in raw and BRIDGE_END in raw:
        raw = raw.split(BRIDGE_START, 1)[1].split(BRIDGE_END, 1)[0].strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else None
    except (TypeError, ValueError):
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(raw[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _match_tool(catalog: list[dict[str, Any]], namespace: str | None, name: str) -> dict[str, Any] | None:
    exact = [
        tool for tool in catalog
        if str(tool.get("name") or "") == name
        and (str(tool.get("namespace") or "") or None) == (namespace or None)
    ]
    if exact:
        return exact[0]
    if namespace is None:
        by_name = [tool for tool in catalog if str(tool.get("name") or "") == name]
        if len(by_name) == 1:
            return by_name[0]
    return None


def _arguments(value: Any) -> str:
    if isinstance(value, str):
        text = value.strip()
        try:
            json.loads(text)
            return text
        except ValueError:
            return json.dumps({"input": value}, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return json.dumps({}, separators=(",", ":"))


def _call_item(call: dict[str, Any], catalog: list[dict[str, Any]]) -> dict[str, Any]:
    namespace = str(call.get("namespace") or "").strip() or None
    name = str(call.get("name") or "").strip()
    tool = _match_tool(catalog, namespace, name)
    if tool is None:
        raise ValueError(f"Model requested undeclared tool {namespace + '::' if namespace else ''}{name or '<missing>'}")
    call_id = "call_" + uuid.uuid4().hex
    kind = str(tool.get("type") or "function")
    if kind == "custom":
        raw_input = call.get("input")
        if raw_input is None:
            raw_input = call.get("arguments")
        item: dict[str, Any] = {
            "id": "ctc_" + uuid.uuid4().hex,
            "type": "custom_tool_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "input": _output_text(raw_input),
        }
    elif kind == "tool_search":
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            arguments = {}
        item = {
            "id": "tsc_" + uuid.uuid4().hex,
            "type": "tool_search_call",
            "status": "completed",
            "call_id": call_id,
            "execution": str(tool.get("execution") or "client"),
            "arguments": arguments,
        }
    else:
        item = {
            "id": "fc_" + uuid.uuid4().hex,
            "type": "function_call",
            "status": "completed",
            "call_id": call_id,
            "name": name,
            "arguments": _arguments(call.get("arguments")),
        }
    if namespace:
        item["namespace"] = namespace
    return item


def _interpret(text: str, catalog: list[dict[str, Any]], body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    value = _json_object(text)
    if value is None:
        return str(text or "").strip(), []
    kind = str(value.get("kind") or "").strip()
    if kind == "tool_calls":
        raw_calls = value.get("calls")
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ValueError("tool_calls envelope contained no calls")
        items = [_call_item(call, catalog) for call in raw_calls if isinstance(call, dict)]
        if not items:
            raise ValueError("tool_calls envelope contained no valid calls")
        if not bool(body.get("parallel_tool_calls", True)):
            items = items[:1]
        return "", items
    if kind == "final":
        return str(value.get("text") or ""), []
    return str(text or "").strip(), []


def _store(app: FastAPI, response: dict[str, Any]) -> None:
    store = getattr(app.state, "responses_v109_context_store", None)
    if not isinstance(store, OrderedDict):
        store = OrderedDict()
        app.state.responses_v109_context_store = store
    response_id = str(response.get("id") or "")
    if response_id:
        store[response_id] = response
        while len(store) > 256:
            store.popitem(last=False)
    if bool(response.get("store", False)):
        public_store = getattr(app.state, "responses_v108_store", None)
        if isinstance(public_store, OrderedDict) and response_id:
            public_store[response_id] = response
            while len(public_store) > 256:
                public_store.popitem(last=False)


def _completed_response(
    response_id: str,
    body: dict[str, Any],
    prompt: str,
    bridge_text: str,
    output_text: str,
    tool_items: list[dict[str, Any]],
    created_at: int,
) -> dict[str, Any]:
    output = tool_items or [_message_item("msg_" + uuid.uuid4().hex, output_text)]
    response = _response(response_id, body, prompt, output_text, output, created_at)
    response["usage"] = _usage(prompt, bridge_text)
    response["output_text"] = output_text
    response["metadata"] = {
        **(body.get("metadata") if isinstance(body.get("metadata"), dict) else {}),
        "chat2api_tool_bridge": "emulated-v109",
    }
    return response


async def _emulated_stream(
    app: FastAPI,
    request: Request,
    state: Any,
    *,
    response_id: str,
    body: dict[str, Any],
    prompt: str,
    catalog: list[dict[str, Any]],
    request_id: str,
    client_id: str,
    timeout: int,
    created_at: int,
) -> AsyncIterator[str]:
    seq = 1
    created = _response(response_id, body, prompt, "", [], created_at, status="in_progress")
    created["usage"] = None
    yield _sse("response.created", seq, response=created)
    try:
        bridge_text, _ = await _collect(state, timeout, False)
        output_text, tool_items = _interpret(bridge_text, catalog, body)
        if tool_items:
            for index, item in enumerate(tool_items):
                seq += 1
                pending = dict(item)
                pending["status"] = "in_progress"
                yield _sse("response.output_item.added", seq, output_index=index, item=pending)
                seq += 1
                yield _sse("response.output_item.done", seq, output_index=index, item=item)
        else:
            message_id = "msg_" + uuid.uuid4().hex
            seq += 1
            yield _sse("response.output_item.added", seq, output_index=0, item=_message_item(message_id, "", "in_progress"))
            seq += 1
            yield _sse("response.content_part.added", seq, item_id=message_id, output_index=0, content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []})
            if output_text:
                seq += 1
                yield _sse("response.output_text.delta", seq, item_id=message_id, output_index=0, content_index=0, delta=output_text, logprobs=[])
            seq += 1
            yield _sse("response.output_text.done", seq, item_id=message_id, output_index=0, content_index=0, text=output_text, logprobs=[])
            seq += 1
            yield _sse("response.content_part.done", seq, item_id=message_id, output_index=0, content_index=0, part={"type": "output_text", "text": output_text, "annotations": [], "logprobs": []})
            seq += 1
            yield _sse("response.output_item.done", seq, output_index=0, item=_message_item(message_id, output_text))
        completed = _completed_response(response_id, body, prompt, bridge_text, output_text, tool_items, created_at)
        _store(app, completed)
        seq += 1
        yield _sse("response.completed", seq, response=completed)
    except asyncio.TimeoutError:
        seq += 1
        failed = _response(response_id, body, prompt, "", [], created_at, status="failed")
        failed["error"] = {"code": "timeout", "message": "Timed out waiting for ChatGPT tool bridge"}
        yield _sse("response.failed", seq, response=failed)
    except Exception as error:
        seq += 1
        failed = _response(response_id, body, prompt, "", [], created_at, status="failed")
        failed["error"] = {"code": "tool_bridge_error", "message": str(error)}
        yield _sse("response.failed", seq, response=failed)
    finally:
        app.state.registry.busy_clients.discard(client_id)
        await app.state.broker.release(request_id)


async def _handle(app: FastAPI, request: Request, body: dict[str, Any]):
    principal = await _authenticate(app, request)
    try:
        prompt, catalog = _bridge_prompt(app, body)
    except ValueError as error:
        return _error(str(error), param="tools", code="responses_tool_bridge_invalid")
    response_id = "resp_" + uuid.uuid4().hex
    created_at = int(time.time())
    try:
        state, request_id, client_id, timeout = await _dispatch(app, request, body, principal, prompt, [])
    except HTTPException:
        raise
    except Exception as error:
        return _error(str(error), status_code=503, code="browser_dispatch_failed")
    if bool(body.get("stream", False)):
        return StreamingResponse(
            _emulated_stream(
                app,
                request,
                state,
                response_id=response_id,
                body=body,
                prompt=prompt,
                catalog=catalog,
                request_id=request_id,
                client_id=client_id,
                timeout=timeout,
                created_at=created_at,
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "X-Chat2API-Tool-Bridge": "emulated-v109"},
        )
    try:
        bridge_text, _ = await _collect(state, timeout, False)
        output_text, tool_items = _interpret(bridge_text, catalog, body)
        response = _completed_response(response_id, body, prompt, bridge_text, output_text, tool_items, created_at)
        _store(app, response)
        return JSONResponse(response, headers={"X-Chat2API-Tool-Bridge": "emulated-v109"})
    except asyncio.TimeoutError:
        try:
            await app.state.registry.send(client_id, {"type": "chat.cancel", "request_id": request_id})
        except Exception:
            pass
        return _error("Timed out waiting for ChatGPT tool bridge", status_code=504, code="timeout")
    except Exception as error:
        return _error(str(error), status_code=502, code="tool_bridge_error")
    finally:
        app.state.registry.busy_clients.discard(client_id)
        await app.state.broker.release(request_id)


class ResponsesEmulatedToolsMiddleware:
    """Intercept Responses requests that need caller-owned tools.

    Native ChatGPT Web Search stays on v108. Function/custom/namespace/tool_search
    requests are converted into a strict textual control protocol for ChatGPT Web,
    then normalized back into canonical Responses output items. Tool execution
    remains entirely caller-owned; chat2api only transports calls and continuations.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], server_app: FastAPI) -> None:
        self.app = app
        self.server_app = server_app

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != "/v1/responses":
            await self.app(scope, receive, send)
            return
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
        try:
            body = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, ValueError, TypeError):
            body = {}
        sent = False

        async def replay_receive():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        if not isinstance(body, dict) or not needs_emulated_tools(body):
            await self.app(scope, replay_receive, send)
            return
        request = Request(scope, replay_receive)
        response = await _handle(self.server_app, request, body)
        await response(scope, replay_receive, send)
