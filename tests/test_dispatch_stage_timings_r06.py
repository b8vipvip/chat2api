"""R-06: separate server admission/send time from Worker browser startup."""
from __future__ import annotations

from pathlib import Path

from app.broker import RequestState


ROOT = Path(__file__).resolve().parents[1]


def test_dispatch_stage_timing_separates_the_160s_browser_gap() -> None:
    state = RequestState(
        request_id="req_r06_slow_start", client_id="ext_r06",
        created_mono=100.0,
        dispatch_send_started_mono=101.25,
        dispatch_send_completed_mono=101.30,
        browser_started_mono=264.30,
        completed_mono=268.30,
        diagnostics={"request_admission_wait_ms": 1300.0},
    )
    durations = state.timings()
    assert durations["request_admission_wait_ms"] == 1300.0
    assert durations["server_send_start_after_broker_ms"] == 1250.0
    assert durations["server_dispatch_send_ms"] == 50.0
    assert durations["server_send_to_browser_started_ms"] == 163000.0
    assert durations["browser_started_ms"] == 164300.0
    assert durations["total_ms"] == 168300.0
    # Browser startup latency is not incorrectly charged to the socket send.
    assert durations["server_dispatch_send_ms"] < durations["server_send_to_browser_started_ms"]


def test_dispatch_timing_missing_or_overlapping_events_are_unknown() -> None:
    pending = RequestState(
        request_id="req_r06_pending", client_id="ext_r06",
        created_mono=100.0, completed_mono=101.0,
    )
    durations = pending.timings()
    assert durations["request_admission_wait_ms"] is None
    assert durations["server_dispatch_send_ms"] is None
    assert durations["server_send_to_browser_started_ms"] is None
    assert durations["server_send_start_after_broker_ms"] is None

    # Fast Worker can produce chat.started while server's async send is still
    # completing. This does not prove a negative network/Worker latency.
    overlap = RequestState(
        request_id="req_r06_overlap", client_id="ext_r06",
        created_mono=100.0,
        dispatch_send_started_mono=101.0,
        dispatch_send_completed_mono=103.0,
        browser_started_mono=102.0,
        completed_mono=104.0,
    )
    assert overlap.timings()["server_send_to_browser_started_ms"] is None

    invalid = RequestState(
        request_id="req_r06_clock", client_id="ext_r06",
        created_mono=100.0,
        dispatch_send_started_mono=99.0,
        dispatch_send_completed_mono=98.0,
        completed_mono=101.0,
    )
    assert invalid.timings()["server_dispatch_send_ms"] is None
    assert invalid.timings()["server_send_start_after_broker_ms"] is None


def test_production_chat_ingress_only_marks_socket_send_not_worker_ack() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    chat = source[
        source.index('    @app.post("/v1/chat/completions")'):
        source.index('    @app.post("/v1/images/generations")')
    ]
    assert "state.dispatch_send_started_mono = time.perf_counter()" in chat
    assert "state.dispatch_send_completed_mono = time.perf_counter()" in chat
    before_dispatch = chat.index("state.dispatch_send_started_mono = time.perf_counter()")
    actual_send = chat.index("await registry.send(", before_dispatch)
    after_dispatch = chat.index("state.dispatch_send_completed_mono = time.perf_counter()")
    assert before_dispatch < actual_send < after_dispatch
    assert "This is not a Worker dispatch ACK." in chat
    # Browser-start truth still comes only from authenticated Worker via Broker,
    # not from a successful socket write or an invented acknowledgement.
    broker = (ROOT / "app/broker.py").read_text(encoding="utf-8")
    assert 'elif event_type in {"chat.started", "image.started"}:' in broker
    assert "state.browser_started_mono = state.browser_started_mono or now" in broker
