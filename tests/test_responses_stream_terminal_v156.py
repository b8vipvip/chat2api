from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.api_keys import ApiPrincipal
from app.responses_model_routing_v108_patch import (
    _ResponsesModelContextMiddleware,
    _SSEStreamObserver,
    _record_telemetry,
    _responses_terminal_status,
)
from app.responses_v108_patch import _dispatch


def _event(kind: str, response: dict) -> bytes:
    return (
        f"event: {kind}\ndata: "
        + json.dumps({"type": kind, "response": response}, separators=(",", ":"))
        + "\n\n"
    ).encode()


def test_sse_observer_records_tool_lifecycle_without_arguments() -> None:
    observer = _SSEStreamObserver()
    tool_item = {
        "id": "fc_123", "type": "function_call", "status": "completed",
        "namespace": "collaboration", "name": "spawn_agent",
        "call_id": "call_123", "arguments": json.dumps({"secret": "do not persist"}),
    }
    raw = (
        "event: response.output_item.added\ndata: "
        + json.dumps({"type": "response.output_item.added", "output_index": 0, "item": tool_item})
        + "\n\n"
        + "event: response.output_item.done\ndata: "
        + json.dumps({"type": "response.output_item.done", "output_index": 0, "item": tool_item})
        + "\n\n"
    ).encode()
    for chunk in [raw[:17], raw[17:105], raw[105:]]:
        observer.feed(chunk)
    assert observer.tool_event_count == 2
    assert [item["event"] for item in observer.tool_events] == [
        "response.output_item.added", "response.output_item.done",
    ]
    assert observer.tool_events[1]["namespace"] == "collaboration"
    assert observer.tool_events[1]["name"] == "spawn_agent"
    assert observer.tool_events[1]["call_id"] == "call_123"
    assert "do not persist" not in str(observer.tool_events)
    assert "arguments" not in str(observer.tool_events)


def test_sse_observer_recovers_split_frames_and_last_delivered_event() -> None:
    observer = _SSEStreamObserver()
    raw = (
        _event("response.created", {"id": "resp_1", "status": "in_progress"})
        + _event("response.completed", {
            "id": "resp_1", "status": "completed", "output_text": "done",
            "usage": {"input_tokens": 3, "output_tokens": 1, "total_tokens": 4},
        })
    )
    for chunk in [raw[:7], raw[7:61], raw[61:115], raw[115:]]:
        observer.feed(chunk)
    assert observer.last_event == "response.completed"
    assert observer.terminal_event == "response.completed"
    assert observer.terminal_response["output_text"] == "done"
    assert observer.event_count == 2
    assert observer.bytes_sent == len(raw)


@pytest.mark.parametrize(
    ("response_status", "error_code", "http_status", "trace", "expected"),
    [
        ("completed", "", 200, {"client_disconnected": True}, ("completed", "response_completed")),
        ("failed", "timeout", 200, {}, ("error", "timeout")),
        ("in_progress", "", 200, {"client_disconnected": True}, ("cancelled", "client_disconnect")),
        ("in_progress", "", 200, {"server_task_cancelled": True}, ("cancelled", "server_task_cancelled")),
        ("in_progress", "", 200, {"send_failed": True}, ("cancelled", "downstream_send_failed")),
        ("in_progress", "", 200, {}, ("error", "missing_terminal_event")),
        ("in_progress", "", 200, {"transport_exception": "TimeoutError"}, ("error", "timeout")),
        ("", "", 400, {}, ("error", "http_error")),
    ],
)
def test_stream_terminal_single_authority(
    response_status: str, error_code: str, http_status: int,
    trace: dict, expected: tuple[str, str],
) -> None:
    assert _responses_terminal_status(
        http_status=http_status, response_status=response_status,
        error_code=error_code, stream=True, trace=trace,
    ) == expected


class _Telemetry:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def get(self, request_id: str) -> dict | None:
        return next((row for row in reversed(self.rows) if row.get("request_id") == request_id), None)

    async def upsert(self, row: dict) -> dict:
        existing = self.get(row["request_id"]) or {}
        merged = {**existing, **row}
        self.rows.append(merged)
        return merged


def test_telemetry_preserves_worker_request_id_and_disconnect_evidence() -> None:
    telemetry = _Telemetry()
    principal = ApiPrincipal(key_id="key_test", name="test", kind="managed", scopes=("chat",))
    trace = {
        "worker_request_id": "resp_req_browser_1",
        "worker_attempt_ids": ["resp_req_browser_1", "resp_repair_2"],
        "worker_client_id": "ext_worker",
        "last_sse_event": "response.created",
        "event_count": 1,
        "client_disconnected": True,
        "disconnect_after_ms": 119950.0,
        "tool_event_count": 1,
        "tool_events": [{
            "event": "response.output_item.done", "type": "function_call",
            "namespace": "collaboration", "name": "spawn_agent",
            "call_id": "call_123",
        }],
    }
    asyncio.run(_record_telemetry(
        SimpleNamespace(state=SimpleNamespace(telemetry=telemetry)),
        request_id="resp_req_browser_1", principal=principal,
        payload={"model": "gpt-6", "stream": True}, prompt="",
        response={"id": "resp_1", "status": "in_progress", "output": [{
            "type": "function_call", "namespace": "collaboration",
            "name": "spawn_agent", "call_id": "call_123",
            "arguments": "{\\\"secret\\\":\\\"private\\\"}",
        }]},
        http_status=200, started_mono=0.0, stream_trace=trace,
    ))
    row = telemetry.rows[-1]
    assert row["worker_request_id"] == "resp_req_browser_1"
    assert row["worker_attempt_ids"] == ["resp_req_browser_1", "resp_repair_2"]
    assert row["worker_client_id"] == "ext_worker"
    assert row["status"] == "cancelled"
    assert row["error"] == "Responses stream ended: client_disconnect"
    assert row["diagnostics"]["response_stream_termination_reason"] == "client_disconnect"
    assert row["diagnostics"]["response_stream_last_sse_event"] == "response.created"
    assert row["diagnostics"]["response_stream_disconnect_after_ms"] == 119950.0
    assert row["diagnostics"]["response_stream_tool_event_count"] == 1
    assert row["diagnostics"]["response_stream_tool_events"][0]["call_id"] == "call_123"
    assert row["diagnostics"]["response_stream_tool_events"][0]["name"] == "spawn_agent"
    assert row["diagnostics"]["response_terminal_tool_items"][0]["call_id"] == "call_123"
    assert "private" not in str(row["diagnostics"]["response_terminal_tool_items"])


@pytest.mark.parametrize(
    ("events", "disconnect", "raise_cancel", "expected_status", "expected_reason", "last_event"),
    [
        (["response.created"], True, False, "cancelled", "client_disconnect", "response.created"),
        (["response.created"], False, True, "cancelled", "server_task_cancelled", "response.created"),
        (["response.created"], False, False, "error", "missing_terminal_event", "response.created"),
        (["response.created", "response.failed"], False, False, "error", "timeout", "response.failed"),
        (["response.created", "response.completed"], False, False, "completed", "response_completed", "response.completed"),
    ],
)
def test_outer_middleware_owns_stream_terminal(
    events: list[str], disconnect: bool, raise_cancel: bool,
    expected_status: str, expected_reason: str, last_event: str,
) -> None:
    telemetry = _Telemetry()
    server = SimpleNamespace(state=SimpleNamespace(
        settings=SimpleNamespace(api_key="master"),
        telemetry=telemetry,
    ))
    payload = json.dumps({"model": "gpt-6", "input": "hello", "stream": True}).encode()
    messages = [
        {"type": "http.request", "body": payload, "more_body": False},
        {"type": "http.disconnect"},
    ]

    async def receive():
        return messages.pop(0) if messages else {"type": "http.disconnect"}

    async def send(message):
        pass

    async def inner(scope, receive, send):
        await receive()
        scope["chat2api.responses_trace"]["worker_request_id"] = "resp_req_worker"
        scope["chat2api.responses_trace"]["worker_attempt_ids"].append("resp_req_worker")
        await send({"type": "http.response.start", "status": 200, "headers": []})
        for kind in events:
            status = "completed" if kind == "response.completed" else "failed" if kind == "response.failed" else "in_progress"
            response = {"id": "resp_1", "status": status}
            if kind == "response.failed":
                response["error"] = {"code": "timeout", "message": "Timeout"}
            frame = _event(kind, response)
            await send({"type": "http.response.body", "body": frame[:8], "more_body": True})
            await send({"type": "http.response.body", "body": frame[8:], "more_body": True})
        if disconnect:
            assert (await receive())["type"] == "http.disconnect"
        if raise_cancel:
            raise asyncio.CancelledError
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    scope = {
        "type": "http", "method": "POST", "path": "/v1/responses",
        "headers": [(b"authorization", b"Bearer master")],
    }
    middleware = _ResponsesModelContextMiddleware(inner, server)

    async def run():
        if raise_cancel:
            with pytest.raises(asyncio.CancelledError):
                await middleware(scope, receive, send)
        else:
            await middleware(scope, receive, send)

    asyncio.run(run())
    row = telemetry.rows[-1]
    assert row["status"] == expected_status
    assert row["worker_request_id"] == "resp_req_worker"
    assert row["diagnostics"]["response_stream_termination_reason"] == expected_reason
    assert row["diagnostics"]["response_stream_last_sse_event"] == last_event
    assert row["diagnostics"]["response_stream_event_count"] == len(events)


def test_dispatch_correlates_worker_and_protocol_repair_attempts() -> None:
    trace = {"worker_attempt_ids": []}
    request = SimpleNamespace(
        headers={"x-chat2api-request-id": "resp_req_original"},
        scope={"chat2api.responses_trace": trace},
    )
    async def create(request_id, client_id):
        return SimpleNamespace(request_id=request_id)

    async def send(client_id, message):
        return None

    registry = SimpleNamespace(
        set_routing_key=lambda key: None,
        resolve_client=lambda requested: "ext_worker",
        busy_clients=set(),
        send=send,
    )
    broker = SimpleNamespace(create=create)
    app = SimpleNamespace(state=SimpleNamespace(
        registry=registry, broker=broker,
        settings=SimpleNamespace(request_timeout_seconds=120),
        telemetry=_Telemetry(),
    ))
    principal = ApiPrincipal(key_id="key_test", name="test", kind="managed", scopes=("chat",))

    async def run():
        await _dispatch(app, request, {"model": "gpt-6"}, principal, "hello", [])
        await _dispatch(app, request, {
            "model": "gpt-6", "_chat2api_parent_request_id": "resp_req_original",
        }, principal, "repair", [], request_id_override="resp_repair_2")

    asyncio.run(run())
    assert trace["worker_request_id"] == "resp_req_original"
    assert trace["worker_attempt_ids"] == ["resp_req_original", "resp_repair_2"]
    assert trace["worker_client_id"] == "ext_worker"
