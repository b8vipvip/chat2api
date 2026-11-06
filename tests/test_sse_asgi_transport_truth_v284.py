"""ASGI receive/send evidence for 2–3s client/SDK SSE disconnect forensics.

The proof is strictly transport-level. Passing bytes to ASGI send is NOT
evidence of client receipt, rendering or application acceptance.
"""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.sse_asgi_transport_truth_v284 import SSETransportTruth

RID = "req_asgi_sse_transport_proof123"
SECRET = "DO_NOT_PERSIST_BEARER_SECRET"


class FakeTelemetry:
    def __init__(self):
        self.rows = {RID: {"request_id": RID, "status": "running",
                           "diagnostics": {"request_admission_stage": "sent"}}}
        self.writes = []

    def get(self, request_id):
        current = self.rows.get(request_id)
        return dict(current) if current else None

    async def update(self, request_id, changes):
        self.writes.append((request_id, changes))
        self.rows[request_id] = {**self.rows[request_id], **changes}
        return self.rows[request_id]


def run(app, *, path="/v1/chat/completions", peer_disconnect=False,
        failing_send=False):
    scope = {"type": "http", "path": path, "method": "POST"}
    outbound = []

    async def receive():
        return {"type": "http.disconnect"} if peer_disconnect else {
            "type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        if failing_send and message["type"] == "http.response.body":
            raise OSError(SECRET)
        outbound.append(message)

    async def execute():
        await app(scope, receive, send)

    if failing_send:
        with pytest.raises(OSError, match=SECRET):
            asyncio.run(execute())
    else:
        asyncio.run(execute())
    return scope, outbound


def test_normal_asgi_stream_does_not_claim_client_received_final_text():
    telemetry = FakeTelemetry()
    state = SimpleNamespace(diagnostics={"worker_terminal": "chat.completed"})

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        scope["chat2api_sse_broker_state"] = state
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream; charset=utf-8")]})
        await send({"type": "http.response.body", "body": b"data: {\"hello\":true}\n\n",
                    "more_body": True})
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n",
                    "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    _, outbound = run(SSETransportTruth(app, telemetry))
    assert len(outbound) == 4
    diagnostics = telemetry.get(RID)["diagnostics"]
    evidence = diagnostics["api_stream_transport_truth"]
    assert diagnostics["api_delivery_state"] == "asgi-complete-unconfirmed"
    assert evidence["asgi_done_frame_sent"] is True
    assert evidence["asgi_final_body_sent"] is True
    assert evidence["asgi_body_chunks_sent"] == 3
    assert evidence["asgi_body_bytes_sent"] == len(b'data: {"hello":true}\n\n') + len(b"data: [DONE]\n\n")
    assert diagnostics["api_stream_peer_disconnect_seen"] is False
    assert diagnostics["request_admission_stage"] == "sent"
    assert state.diagnostics["worker_terminal"] == "chat.completed"


def test_real_http_disconnect_before_done_is_captured_from_receive():
    telemetry = FakeTelemetry()
    state = SimpleNamespace(diagnostics={})

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        scope["chat2api_sse_broker_state"] = state
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: {}\n\n", "more_body": True})
        assert (await receive())["type"] == "http.disconnect"

    scope, _ = run(SSETransportTruth(app, telemetry), peer_disconnect=True)
    assert scope["chat2api_sse_transport_truth"]["http_disconnect_seen"] is True
    row = telemetry.get(RID)
    evidence = row["diagnostics"]["api_stream_transport_truth"]
    assert row["status"] == "running"  # Worker/Broker terminal stays authoritative
    assert row["diagnostics"]["api_delivery_state"] == "disconnected"
    assert row["diagnostics"]["api_stream_peer_disconnect_seen"] is True
    assert evidence["http_disconnect_ms"] is not None
    assert evidence["asgi_done_frame_sent"] is False
    assert state.diagnostics["api_delivery_state"] == "disconnected"
    assert state.diagnostics["api_stream_transport_truth"]["http_disconnect_seen"] is True


def test_send_error_keeps_actual_exception_and_classifies_delivery_state():
    telemetry = FakeTelemetry()
    state = SimpleNamespace(diagnostics={})

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        scope["chat2api_sse_broker_state"] = state
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": SECRET.encode(), "more_body": True})

    run(SSETransportTruth(app, telemetry), failing_send=True)
    diag = telemetry.get(RID)["diagnostics"]
    assert diag["api_delivery_state"] == "asgi-send-error"
    assert diag["api_stream_transport_truth"]["asgi_send_error"] == "OSError"
    assert diag["api_stream_transport_truth"]["asgi_body_bytes_sent"] == 0
    assert SECRET not in str(diag)


def test_generated_sse_json_text_that_mentions_done_is_not_done_frame():
    telemetry = FakeTelemetry()

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body",
                    "body": b'data: {"content":"data: [DONE]"}\n\n', "more_body": False})

    run(SSETransportTruth(app, telemetry))
    diag = telemetry.get(RID)["diagnostics"]
    assert diag["api_stream_transport_truth"]["asgi_done_frame_sent"] is False
    assert diag["api_delivery_state"] == "asgi-incomplete"


def test_unrelated_route_and_nonstream_json_are_passthrough_without_telemetry():
    for path, mime in (("/healthz", b"text/event-stream"),
                       ("/v1/chat/completions", b"application/json")):
        telemetry = FakeTelemetry()

        async def app(scope, receive, send):
            scope["chat2api_sse_request_id"] = RID
            await send({"type": "http.response.start", "status": 200,
                        "headers": [(b"content-type", mime)]})
            await send({"type": "http.response.body", "body": b"ok", "more_body": False})

        run(SSETransportTruth(app, telemetry), path=path)
        assert telemetry.writes == []


def test_sse_transport_fields_are_bounded_and_do_not_store_credential_payload():
    telemetry = FakeTelemetry()

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body",
                    "body": b"data: " + SECRET.encode() * 1000 + b"\n\n",
                    "more_body": True})

    run(SSETransportTruth(app, telemetry))
    diag = telemetry.get(RID)["diagnostics"]
    assert SECRET not in str(diag)
    assert diag["api_stream_transport_truth"]["asgi_body_bytes_sent"] > 5000
    assert diag["api_delivery_state"] == "asgi-incomplete"


def test_main_wires_same_request_broker_state_and_no_request_body_copy():
    server = (Path(__file__).resolve().parents[1] / "app/main.py").read_text("utf8")
    assert "app.add_middleware(SSETransportTruth, telemetry=telemetry)" in server
    assert 'request.scope["chat2api_sse_broker_state"] = state' in server
    assert 'request.scope["chat2api_sse_request_id"] = request_id' in server
    assert "chat2api_sse_transport_truth" in server


@pytest.mark.parametrize(
    ("frames", "expected_done"),
    [
        # ASGI servers are free to combine several SSE events into one send.
        ([b'data: {"step":1}\n\ndata: [DONE]\n\n'], True),
        # TCP/ASGI boundaries need not align with an SSE line or event.
        ([b"data: [DO", b"NE]\r\n", b"\r\n"], True),
        ([b"data: [DONE]\n", b"\n"], True),
        # A textual mention in another data value must not be a DONE event.
        ([b'data: {"content":"data: [DONE]"}\n\n'], False),
        # A second data field invalidates the whole candidate event.
        ([b"data: [DONE]\ndata: more\n\n"], False),
        # Closing the event with no blank line is not a complete SSE frame.
        ([b"data: [DONE]\n"], False),
        # Huge data fields are skipped without keeping response bytes.
        ([b"data: " + b"X" * 100_000 + b"\n\n", b"data: [DONE]\n\n"], True),
    ],
)
def test_sse_done_detection_follows_event_framing_across_body_sends(
    frames, expected_done,
):
    telemetry = FakeTelemetry()

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        for frame in frames:
            await send({"type": "http.response.body", "body": frame,
                        "more_body": True})
        await send({"type": "http.response.body", "body": b"",
                    "more_body": False})

    run(SSETransportTruth(app, telemetry))
    evidence = telemetry.get(RID)["diagnostics"]["api_stream_transport_truth"]
    assert evidence["asgi_done_frame_sent"] is expected_done
    assert evidence["asgi_body_bytes_sent"] == sum(map(len, frames))
    assert evidence["asgi_receive_calls"] == 0
    assert evidence["asgi_receive_messages"] == 0
    assert evidence["asgi_receive_last_ms"] is None
    assert telemetry.get(RID)["diagnostics"]["api_delivery_state"] == (
        "asgi-complete-unconfirmed" if expected_done else "asgi-incomplete"
    )


def test_disconnect_absence_has_observation_coverage_not_false_negative_proof():
    telemetry = FakeTelemetry()

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        assert (await receive())["type"] == "http.request"
        await send({"type": "http.response.body", "body": b": alive\n\n",
                    "more_body": True})

    run(SSETransportTruth(app, telemetry))
    evidence = telemetry.get(RID)["diagnostics"]["api_stream_transport_truth"]
    assert evidence["http_disconnect_seen"] is False
    assert evidence["asgi_receive_calls"] == 1
    assert evidence["asgi_receive_messages"] == 1
    assert evidence["asgi_receive_last_ms"] is not None


def test_done_detection_does_not_scan_failed_asgi_send():
    telemetry = FakeTelemetry()

    async def app(scope, receive, send):
        scope["chat2api_sse_request_id"] = RID
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: [DONE]\n\n",
                    "more_body": True})

    run(SSETransportTruth(app, telemetry), failing_send=True)
    evidence = telemetry.get(RID)["diagnostics"]["api_stream_transport_truth"]
    assert evidence["asgi_done_frame_sent"] is False
    assert evidence["asgi_send_error"] == "OSError"
