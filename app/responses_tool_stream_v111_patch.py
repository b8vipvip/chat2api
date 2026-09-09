from __future__ import annotations

import asyncio
import uuid
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request

from . import responses_emulated_tools_v109_patch as v109
from .responses_v108_patch import _collect, _message_item, _response, _sse


PATCH_REVISION = 111


def _pending_item(item: dict[str, Any]) -> dict[str, Any]:
    pending = dict(item)
    pending["status"] = "in_progress"
    if str(item.get("type") or "") == "custom_tool_call":
        pending["input"] = ""
    elif str(item.get("type") or "") == "function_call":
        pending["arguments"] = ""
    return pending


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


async def _emulated_stream_v111(
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
                    item=item,
                )
        else:
            message_id = "msg_" + uuid.uuid4().hex
            seq += 1
            yield _sse("response.output_item.added", seq, response_id=response_id, output_index=0, item=_message_item(message_id, "", "in_progress"))
            seq += 1
            yield _sse("response.content_part.added", seq, response_id=response_id, item_id=message_id, output_index=0, content_index=0, part={"type": "output_text", "text": "", "annotations": [], "logprobs": []})
            if output_text:
                seq += 1
                yield _sse("response.output_text.delta", seq, response_id=response_id, item_id=message_id, output_index=0, content_index=0, delta=output_text, logprobs=[])
            seq += 1
            yield _sse("response.output_text.done", seq, response_id=response_id, item_id=message_id, output_index=0, content_index=0, text=output_text, logprobs=[])
            seq += 1
            yield _sse("response.content_part.done", seq, response_id=response_id, item_id=message_id, output_index=0, content_index=0, part={"type": "output_text", "text": output_text, "annotations": [], "logprobs": []})
            seq += 1
            yield _sse("response.output_item.done", seq, response_id=response_id, output_index=0, item=_message_item(message_id, output_text))
        completed = v109._completed_response(response_id, body, prompt, bridge_text, output_text, tool_items, created_at)
        completed.setdefault("metadata", {})["chat2api_tool_stream"] = "responses-v111-canonical-events"
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


def install_responses_tool_stream_v111_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "responses_tool_stream_v111_installed", False):
        return app
    v109._emulated_stream = _emulated_stream_v111
    app.state.responses_tool_stream_v111_installed = True
    app.state.responses_tool_stream_revision = PATCH_REVISION
    return app
