"""Hosted Responses image tool delegates to the existing one-shot Images engine."""
from __future__ import annotations

import asyncio
import base64
import json
import time
from collections import OrderedDict
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api_keys import ApiPrincipal
from app.responses_emulated_tools_v109_patch import needs_emulated_tools
from app.responses_image_tool_v182 import (
    handle_image_tool, is_image_generation_request, validate_image_tool,
)
from app.responses_model_routing_v108_patch import _record_telemetry


def request():
    return Request({
        "type": "http", "method": "POST", "scheme": "https",
        "path": "/v1/responses", "server": ("chat2api.example", 443),
        "query_string": b"", "headers": [],
        "chat2api.responses_trace": {"worker_attempt_ids": []},
    })


def principal(scopes=("chat", "images")):
    return ApiPrincipal(key_id="key-owner", name="owner", kind="managed", scopes=scopes)


def app_with_fake_engine():
    app = FastAPI()
    app.state.responses_v108_store = OrderedDict()
    calls = []

    async def execute(**kwargs):
        calls.append(kwargs)
        return {
            "data": [{"b64_json": base64.b64encode(b"generated-example").decode()}],
            "chat2api": {
                "request_id": kwargs["x_chat2api_request_id"],
                "client_id": "ext_1",
                "owned_media": [{
                    "id": "med_123",
                    "url": "https://chat2api.example/api/media/med_123/TOKEN",
                    "thumbnail_url": "https://chat2api.example/api/media/med_123/TOKEN/thumbnail",
                }],
            },
        }

    app.state.image_generation_executor = execute
    return app, calls


def test_image_tool_is_native_not_emulated_and_single_worker_dispatch():
    payload = {"model": "gpt-6", "input": "生成一个头像",
               "tools": [{"type": "image_generation"}]}
    assert is_image_generation_request(payload)
    assert needs_emulated_tools(payload) is False
    app, calls = app_with_fake_engine()
    req = request()
    result = asyncio.run(handle_image_tool(app, req, payload, principal(), "生成一个头像"))
    assert isinstance(result, JSONResponse)
    assert result.status_code == 200
    response = json.loads(result.body)
    assert response["object"] == "response"
    assert response["model"] == "gpt-6"
    assert response["output_text"] == ""
    assert len(response["output"]) == 1
    item = response["output"][0]
    assert item["type"] == "image_generation_call"
    assert item["status"] == "completed"
    assert base64.b64decode(item["result"]) == b"generated-example"
    assert response["chat2api"]["owned_media"][0]["thumbnail_url"].endswith("/thumbnail")
    assert len(calls) == 1
    assert calls[0]["body"].model == "gpt-image"
    assert calls[0]["body"].response_format == "b64_json"
    assert calls[0]["principal"].key_id == "key-owner"
    assert req.scope["chat2api.responses_trace"]["worker_request_id"].startswith("imgreq_")
    assert len(req.scope["chat2api.responses_trace"]["worker_attempt_ids"]) == 1
    assert app.state.responses_v108_store[response["id"]] == response


def test_image_tool_stream_has_standard_events_and_no_second_dispatch():
    payload = {"model": "gpt-6", "input": "头像", "stream": True,
               "tools": [{"type": "image_generation"}]}
    app, calls = app_with_fake_engine()

    async def collect():
        result = await handle_image_tool(app, request(), payload, principal(), "头像")
        assert isinstance(result, StreamingResponse)
        chunks = []
        async for chunk in result.body_iterator:
            chunks.append(chunk)
        return chunks

    frames = asyncio.run(collect())
    events = [json.loads(frame.split("data: ", 1)[1]) for frame in frames]
    types = [item["type"] for item in events]
    assert types == [
        "response.created", "response.output_item.added",
        "response.image_generation_call.completed", "response.output_item.done",
        "response.completed",
    ]
    assert events[-1]["response"]["output"][0]["type"] == "image_generation_call"
    assert events[-1]["response"]["status"] == "completed"
    assert len(calls) == 1


def test_image_tool_validates_permissions_and_unsupported_parameters():
    import pytest
    payload = {"input": "头像", "tools": [{"type": "image_generation", "quality": "high"}]}
    with pytest.raises(ValueError, match="Unsupported image_generation"):
        validate_image_tool(payload)
    with pytest.raises(ValueError, match="only tool"):
        validate_image_tool({"tools": [
            {"type": "image_generation"}, {"type": "function", "name": "foo"},
        ]})
    app, calls = app_with_fake_engine()
    result = asyncio.run(handle_image_tool(
        app, request(), {"input": "头像", "tools": [{"type": "image_generation"}]},
        principal(("chat",)), "头像",
    ))
    # The same permission gate as Images is applied before any Worker dispatch.
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as info:
        asyncio.run(handle_image_tool(
            app, request(), {"input": "头像", "tools": [{"type": "image_generation"}]},
            principal(("chat",)), "头像",
        ))
    assert info.value.status_code == 403
    assert calls == []


def test_delegated_image_response_is_not_double_billed():
    captured = []
    async def upsert(record):
        captured.append(record)
    app = FastAPI()
    app.state.telemetry = SimpleNamespace(get=lambda request_id: None, upsert=upsert)
    app.state.settings = SimpleNamespace(request_timeout_seconds=300)
    response = {"id": "resp_123", "status": "completed", "output_text": "",
                "output": [{"id": "ig_123", "type": "image_generation_call", "status": "completed"}],
                "usage": {"input_tokens": 123, "output_tokens": 0, "total_tokens": 123}}
    asyncio.run(_record_telemetry(
        app, request_id="resp_req_parent12345",
        principal=principal(), payload={"model": "gpt-6",
        "input": "头像", "tools": [{"type": "image_generation"}]},
        prompt="头像", response=response, http_status=200, started_mono=time.perf_counter(),
        stream_trace={"worker_request_id": "imgreq_child12345",
                      "worker_attempt_ids": ["imgreq_child12345"]},
    ))
    assert len(captured) == 1
    assert captured[0]["status"] == "completed"
    assert captured[0]["usage"]["total_tokens"] == 0
    assert captured[0]["worker_request_id"] == "imgreq_child12345"
