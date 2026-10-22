"""Responses image_generation protocol adapter.

The existing /v1/images/generations engine is the ONLY image Worker/Broker
request owner. This module exclusively validates and adapts that completed
artifact to OpenAI's Responses image_generation_call response shape.
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from collections import OrderedDict
from typing import Any, AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .api_keys import ApiPrincipal
from .models import ImageGenerationRequest
from .responses_v108_patch import _error, _response, _sse


def is_image_generation_request(body: dict[str, Any]) -> bool:
    return any(
        isinstance(item, dict) and item.get("type") == "image_generation"
        for item in (body.get("tools") if isinstance(body.get("tools"), list) else [])
    )


def validate_image_tool(body: dict[str, Any]) -> dict[str, Any]:
    tools = body.get("tools")
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict):
        raise ValueError("image_generation must be the only tool in this request")
    tool = tools[0]
    if tool.get("type") != "image_generation":
        raise ValueError("image_generation tool is required")
    unsupported = set(tool) - {"type", "size"}
    if unsupported:
        raise ValueError("Unsupported image_generation tool parameters: " + ", ".join(sorted(unsupported)))
    choice = body.get("tool_choice", "auto")
    if choice not in ("auto", "required") and not (
        isinstance(choice, dict) and choice.get("type") == "image_generation"
    ):
        raise ValueError("tool_choice must select image_generation, auto, or required")
    for key in ("background", "previous_response_id", "parallel_tool_calls"):
        if key == "parallel_tool_calls":
            continue
        if body.get(key):
            raise ValueError(f"{key} is not implemented for image_generation")
    if body.get("input") is None:
        raise ValueError("input is required")
    # Image edits/vision inputs are not yet mapped into the canonical Images
    # attachment contract. Fail explicitly instead of discarding user images.
    input_value = body.get("input")
    for entry in input_value if isinstance(input_value, list) else []:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") in {"input_image", "input_file"}:
            raise ValueError("image_generation input images/files are not implemented")
        for part in entry.get("content") if isinstance(entry.get("content"), list) else []:
            if isinstance(part, dict) and part.get("type") in {"input_image", "input_file", "image_url", "file"}:
                raise ValueError("image_generation input images/files are not implemented")
    return tool


def _store(app: FastAPI, response: dict[str, Any], body: dict[str, Any]) -> None:
    if not bool(body.get("store", True)):
        return
    store: OrderedDict[str, dict[str, Any]] = app.state.responses_v108_store
    store[response["id"]] = response
    while len(store) > 256:
        store.popitem(last=False)


async def _run_existing_image_request(app: FastAPI, request: Request, body: dict[str, Any],
                                      principal: ApiPrincipal, prompt: str, tool: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if principal.kind != "master" and "images" not in principal.scopes:
        raise HTTPException(status_code=403, detail="API key does not have images permission")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("image_generation input must contain non-empty text")
    executor = getattr(app.state, "image_generation_executor", None)
    if not callable(executor):
        raise RuntimeError("The canonical image-generation engine is unavailable")
    timeout = body.get("timeout") or 600
    try:
        timeout = max(30, min(900, int(timeout)))
    except (TypeError, ValueError) as error:
        raise ValueError("timeout must be an integer number of seconds") from error
    request_id = "imgreq_" + uuid.uuid4().hex
    image_body = ImageGenerationRequest(
        model="gpt-image", prompt=prompt, response_format="b64_json",
        size=tool.get("size"), timeout=timeout, n=1,
    )
    result = await executor(
        body=image_body, request=request,
        x_chat2api_client=body.get("client_id") or request.headers.get("x-chat2api-client"),
        x_chat2api_request_id=request_id, principal=principal,
    )
    records = result.get("data") if isinstance(result, dict) else None
    if not isinstance(records, list) or not records or not isinstance(records[0], dict):
        raise RuntimeError("Image engine completed without a standard image payload")
    encoded = records[0].get("b64_json")
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("Image engine did not produce a base64 result")
    try:
        base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise RuntimeError("Image engine produced invalid base64") from error
    meta = result.get("chat2api") if isinstance(result.get("chat2api"), dict) else {}
    trace = request.scope.get("chat2api.responses_trace")
    if isinstance(trace, dict):
        trace["worker_request_id"] = str(meta.get("request_id") or request_id)
        attempts = trace.setdefault("worker_attempt_ids", [])
        if trace["worker_request_id"] not in attempts:
            attempts.append(trace["worker_request_id"])
        if meta.get("client_id"):
            trace["worker_client_id"] = meta["client_id"]
    return encoded, dict(meta)


def _result_response(response_id: str, body: dict[str, Any], prompt: str, created_at: int,
                     item: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    response = _response(response_id, body, prompt, "", [item], created_at)
    if meta:
        # Explicitly namespaced extra metadata; never replace standard result.
        response["chat2api"] = {
            "request_id": meta.get("request_id"),
            "owned_media": meta.get("owned_media") or [],
        }
    return response


async def handle_image_tool(app: FastAPI, request: Request, body: dict[str, Any],
                            principal: ApiPrincipal, prompt: str):
    try:
        tool = validate_image_tool(body)
    except ValueError as error:
        return _error(str(error), param="tools", code="image_generation_invalid")
    if principal.kind != "master" and "images" not in principal.scopes:
        raise HTTPException(status_code=403, detail="API key does not have images permission")
    response_id = "resp_" + uuid.uuid4().hex
    item_id = "ig_" + uuid.uuid4().hex
    created_at = int(time.time())
    if not bool(body.get("stream", False)):
        try:
            encoded, meta = await _run_existing_image_request(app, request, body, principal, prompt, tool)
        except ValueError as error:
            return _error(str(error), status_code=400, param="input", code="invalid_image_request")
        except HTTPException:
            raise
        except Exception as error:
            return _error(str(error), status_code=502, code="image_generation_error")
        item = {"id": item_id, "type": "image_generation_call", "status": "completed", "result": encoded}
        response = _result_response(response_id, body, prompt, created_at, item, meta)
        _store(app, response, body)
        return JSONResponse(response)

    async def stream() -> AsyncIterator[str]:
        seq = 1
        initial = _response(response_id, body, prompt, "", [], created_at, status="in_progress")
        initial["usage"] = None
        yield _sse("response.created", seq, response=initial)
        seq += 1
        provisional = {"id": item_id, "type": "image_generation_call", "status": "in_progress", "result": None}
        yield _sse("response.output_item.added", seq, output_index=0, item=provisional)
        try:
            encoded, meta = await _run_existing_image_request(app, request, body, principal, prompt, tool)
            seq += 1
            yield _sse("response.image_generation_call.completed", seq, output_index=0, item_id=item_id)
            seq += 1
            item = {"id": item_id, "type": "image_generation_call", "status": "completed", "result": encoded}
            yield _sse("response.output_item.done", seq, output_index=0, item=item)
            response = _result_response(response_id, body, prompt, created_at, item, meta)
            _store(app, response, body)
            seq += 1
            yield _sse("response.completed", seq, response=response)
        except Exception as error:
            failed = _response(response_id, body, prompt, "", [], created_at, status="failed")
            failed["error"] = {"code": "image_generation_error", "message": str(error)}
            seq += 1
            yield _sse("response.failed", seq, response=failed)

    return StreamingResponse(
        stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
