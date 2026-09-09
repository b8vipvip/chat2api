from __future__ import annotations

import asyncio
import re
import uuid
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request

from . import responses_emulated_tools_v109_patch as v109
from .responses_v108_patch import _collect, _message_item, _response, _sse


PATCH_REVISION = 113
_BASE_JSON_OBJECT = v109._json_object


def _pending_item(item: dict[str, Any]) -> dict[str, Any]:
    pending = dict(item)
    pending["status"] = "in_progress"
    if str(item.get("type") or "") == "custom_tool_call":
        pending["input"] = ""
    elif str(item.get("type") or "") == "function_call":
        pending["arguments"] = ""
    return pending


def _canonical_done_item(item: dict[str, Any]) -> dict[str, Any]:
    """Match the minimal Responses items consumed by Codex 0.149."""
    done = dict(item)
    kind = str(done.get("type") or "")
    if kind in {"function_call", "custom_tool_call", "tool_search_call"}:
        done.pop("id", None)
        done.pop("status", None)
    return done


def _decode_lenient_json_string(value: str) -> str:
    """Decode JSON escapes while tolerating raw quotes inside a model-produced string."""
    out: list[str] = []
    index = 0
    escapes = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    while index < len(value):
        char = value[index]
        if char != "\\" or index + 1 >= len(value):
            out.append(char)
            index += 1
            continue
        nxt = value[index + 1]
        if nxt == "u" and index + 5 < len(value):
            code = value[index + 2 : index + 6]
            if re.fullmatch(r"[0-9A-Fa-f]{4}", code):
                out.append(chr(int(code, 16)))
                index += 6
                continue
        if nxt in escapes:
            out.append(escapes[nxt])
            index += 2
            continue
        # Preserve unknown escapes literally. Custom tool payloads can contain
        # programming-language escapes that are not JSON escapes.
        out.append("\\")
        index += 1
    return "".join(out)


def _recover_single_custom_tool_envelope(text: str) -> dict[str, Any] | None:
    """Recover the common malformed custom-tool envelope emitted by ChatGPT.

    Long raw JavaScript custom-tool inputs sometimes contain unescaped double
    quotes. That makes the otherwise correct bridge JSON invalid and previously
    downgraded a real tool call into assistant text, so Codex never executed it.
    Only recover the narrow one-call shape and leave every other malformed
    envelope fail-closed.
    """
    raw = str(text or "").strip()
    if v109.BRIDGE_START in raw and v109.BRIDGE_END in raw:
        raw = raw.split(v109.BRIDGE_START, 1)[1].split(v109.BRIDGE_END, 1)[0].strip()

    outer = re.fullmatch(
        r'\s*\{\s*"kind"\s*:\s*"tool_calls"\s*,\s*"calls"\s*:\s*\[\s*\{(?P<body>.*)\}\s*\]\s*\}\s*',
        raw,
        flags=re.DOTALL,
    )
    if outer is None:
        return None
    body = outer.group("body")
    call = re.fullmatch(
        r'\s*(?:(?:"namespace"\s*:\s*(?:"(?P<namespace>[^"]+)"|null)\s*,\s*)?)'
        r'"name"\s*:\s*"(?P<name>[^"]+)"\s*,\s*'
        r'"input"\s*:\s*"(?P<input>.*)"\s*',
        body,
        flags=re.DOTALL,
    )
    if call is None:
        return None
    result: dict[str, Any] = {
        "name": call.group("name"),
        "input": _decode_lenient_json_string(call.group("input")),
    }
    namespace = call.group("namespace")
    if namespace:
        result["namespace"] = namespace
    return {"kind": "tool_calls", "calls": [result]}


def _json_object_with_custom_recovery(text: str) -> dict[str, Any] | None:
    parsed = _BASE_JSON_OBJECT(text)
    if parsed is not None:
        return parsed
    return _recover_single_custom_tool_envelope(text)


def _restore_tool_namespaces(
    tool_items: list[dict[str, Any]],
    catalog: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Restore a uniquely-known namespace when the bridge call omitted it."""
    restored: list[dict[str, Any]] = []
    for item in tool_items:
        current = dict(item)
        if not str(current.get("namespace") or ""):
            name = str(current.get("name") or "")
            kind = str(current.get("type") or "")
            catalog_kind = {
                "custom_tool_call": "custom",
                "function_call": "function",
                "tool_search_call": "tool_search",
            }.get(kind)
            matches = [
                tool
                for tool in catalog
                if str(tool.get("name") or "") == name
                and (catalog_kind is None or str(tool.get("type") or "") == catalog_kind)
                and str(tool.get("namespace") or "")
            ]
            namespaces = {str(tool.get("namespace") or "") for tool in matches}
            if len(namespaces) == 1:
                current["namespace"] = namespaces.pop()
        restored.append(current)
    return restored


def _canonical_response_tool_items(tool_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_canonical_done_item(item) for item in tool_items]


def _tool_argument_events(
    response_id: str,
    output_index: int,
    item: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    kind = str(item.get("type") or "")
    item_id = str(item.get("id") or "")
    call_id = str(item.get("call_id") or "")
    if kind == "custom_tool_call":
        value = str(item.get("input") or "")
        return [
            (
                "response.custom_tool_call_input.delta",
                {
                    "response_id": response_id,
                    "item_id": item_id,
                    "call_id": call_id,
                    "output_index": output_index,
                    "delta": value,
                },
            ),
            (
                "response.custom_tool_call_input.done",
                {
                    "response_id": response_id,
                    "item_id": item_id,
                    "call_id": call_id,
                    "output_index": output_index,
                    "input": value,
                },
            ),
        ]
    if kind == "function_call":
        value = str(item.get("arguments") or "")
        return [
            (
                "response.function_call_arguments.delta",
                {
                    "response_id": response_id,
                    "item_id": item_id,
                    "call_id": call_id,
                    "name": str(item.get("name") or ""),
                    "output_index": output_index,
                    "delta": value,
                },
            ),
            (
                "response.function_call_arguments.done",
                {
                    "response_id": response_id,
                    "item_id": item_id,
                    "call_id": call_id,
                    "name": str(item.get("name") or ""),
                    "output_index": output_index,
                    "arguments": value,
                },
            ),
        ]
    return []


def _mark_tool_follow_up(response: dict[str, Any], tool_items: list[dict[str, Any]]) -> None:
    if tool_items:
        response["end_turn"] = False
        response.setdefault("metadata", {})["chat2api_tool_stream"] = "responses-v113-codex-0149"


async def _emulated_stream_v113(
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
        output_text, tool_items = v109._interpret(bridge_text, catalog, body)
        tool_items = _restore_tool_namespaces(tool_items, catalog)
        if tool_items:
            for index, item in enumerate(tool_items):
                seq += 1
                yield _sse(
                    "response.output_item.added",
                    seq,
                    response_id=response_id,
                    output_index=index,
                    item=_pending_item(item),
                )
                for event_type, payload in _tool_argument_events(response_id, index, item):
                    seq += 1
                    yield _sse(event_type, seq, **payload)
                seq += 1
                yield _sse(
                    "response.output_item.done",
                    seq,
                    response_id=response_id,
                    output_index=index,
                    item=_canonical_done_item(item),
                )
        else:
            message_id = "msg_" + uuid.uuid4().hex
            seq += 1
            yield _sse(
                "response.output_item.added",
                seq,
                response_id=response_id,
                output_index=0,
                item=_message_item(message_id, "", "in_progress"),
            )
            seq += 1
            yield _sse(
                "response.content_part.added",
                seq,
                response_id=response_id,
                item_id=message_id,
                output_index=0,
                content_index=0,
                part={"type": "output_text", "text": "", "annotations": [], "logprobs": []},
            )
            if output_text:
                seq += 1
                yield _sse(
                    "response.output_text.delta",
                    seq,
                    response_id=response_id,
                    item_id=message_id,
                    output_index=0,
                    content_index=0,
                    delta=output_text,
                    logprobs=[],
                )
            seq += 1
            yield _sse(
                "response.output_text.done",
                seq,
                response_id=response_id,
                item_id=message_id,
                output_index=0,
                content_index=0,
                text=output_text,
                logprobs=[],
            )
            seq += 1
            yield _sse(
                "response.content_part.done",
                seq,
                response_id=response_id,
                item_id=message_id,
                output_index=0,
                content_index=0,
                part={"type": "output_text", "text": output_text, "annotations": [], "logprobs": []},
            )
            seq += 1
            yield _sse(
                "response.output_item.done",
                seq,
                response_id=response_id,
                output_index=0,
                item=_message_item(message_id, output_text),
            )

        completed_items = _canonical_response_tool_items(tool_items) if tool_items else tool_items
        completed = v109._completed_response(
            response_id,
            body,
            prompt,
            bridge_text,
            output_text,
            completed_items,
            created_at,
        )
        _mark_tool_follow_up(completed, tool_items)
        if not tool_items:
            completed.setdefault("metadata", {})["chat2api_tool_stream"] = "responses-v113-codex-0149"
        v109._store(app, completed)
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


def install_responses_tool_stream_v113_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v113_installed", False):
        return app
    v109._json_object = _json_object_with_custom_recovery
    v109._emulated_stream = _emulated_stream_v113
    app.state.responses_tool_stream_v113_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app
