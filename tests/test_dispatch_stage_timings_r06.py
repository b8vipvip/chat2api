"""R-06: separate server admission/send time from Worker browser startup."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

from app.broker import RequestBroker, RequestState
from app.registry import ClientRegistry


ROOT = Path(__file__).resolve().parents[1]


def test_dispatch_stage_timing_separates_the_160s_browser_gap() -> None:
    state = RequestState(
        request_id="req_r06_slow_start", client_id="ext_r06",
        created_mono=100.0,
        dispatch_socket_lock_wait_started_mono=101.0,
        dispatch_socket_lock_acquired_mono=101.25,
        dispatch_send_started_mono=101.30,
        dispatch_send_completed_mono=101.35,
        worker_content_ack_mono=105.0,
        browser_started_mono=264.30,
        completed_mono=268.30,
        diagnostics={"request_admission_wait_ms": 1300.0},
    )
    durations = state.timings()
    assert durations["request_admission_wait_ms"] == 1300.0
    assert durations["server_dispatch_socket_lock_wait_ms"] == 250.0
    assert durations["server_send_start_after_broker_ms"] == 1300.0
    assert durations["server_dispatch_send_ms"] == 50.0
    assert durations["server_send_start_to_worker_content_ack_ms"] == 3700.0
    assert durations["server_send_to_worker_content_ack_ms"] == 3650.0
    assert durations["worker_content_ack_to_browser_started_ms"] == 159300.0
    assert durations["server_send_to_browser_started_ms"] == 162950.0
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
    assert durations["server_dispatch_socket_lock_wait_ms"] is None
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
        worker_content_ack_mono=103.5,
        browser_started_mono=102.0,
        completed_mono=104.0,
    )
    assert overlap.timings()["server_send_start_to_worker_content_ack_ms"] == 2500.0
    assert overlap.timings()["server_send_to_worker_content_ack_ms"] == 500.0
    assert overlap.timings()["worker_content_ack_to_browser_started_ms"] is None
    assert overlap.timings()["server_send_to_browser_started_ms"] is None

    # The content ACK can race the completion of the server's async socket
    # send. The start-to-ACK clock remains valid; completion-to-ACK is unknown.
    early_ack = RequestState(
        request_id="req_r06_early_ack", client_id="ext_r06",
        created_mono=100.0,
        dispatch_send_started_mono=101.0,
        dispatch_send_completed_mono=103.0,
        worker_content_ack_mono=102.0,
        browser_started_mono=104.0,
        completed_mono=105.0,
    )
    assert early_ack.timings()["server_send_start_to_worker_content_ack_ms"] == 1000.0
    assert early_ack.timings()["server_send_to_worker_content_ack_ms"] is None
    assert early_ack.timings()["worker_content_ack_to_browser_started_ms"] == 2000.0
    assert early_ack.timings()["server_send_to_browser_started_ms"] == 1000.0

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
    assert "await registry.send(" in chat
    assert "dispatch_send_started_mono = time.perf_counter()" not in chat
    registry = (ROOT / "app/registry.py").read_text(encoding="utf-8")
    assert "dispatch_socket_lock_wait_started_mono = time.perf_counter()" in registry
    assert "dispatch_socket_lock_acquired_mono = time.perf_counter()" in registry
    assert "dispatch_send_started_mono = time.perf_counter()" in registry
    assert "dispatch_send_completed_mono = time.perf_counter()" in registry
    assert "server_dispatch_socket_lock_wait_ms" in (ROOT / "app/broker.py").read_text(encoding="utf-8")
    # Browser-start truth still comes only from authenticated Worker via Broker,
    # not from a successful socket write or an invented acknowledgement.
    background = (ROOT / "chrome_extension/background.js").read_text(encoding="utf-8")
    ack = background.index("worker_content_dispatch_ack_v1: true")
    send_ack = background.index("await chrome.tabs.sendMessage(tab.id, {", background.index('if (message.type === "chat.request")'))
    assert send_ack < ack
    assert 'response.controller === "request-v6" && !response.reroute_pending' in background
    assert "worker_content_dispatch_ack_source: \"background-tabs-sendMessage-request-v6\"" in background

    broker = (ROOT / "app/broker.py").read_text(encoding="utf-8")
    assert 'elif event_type in {"chat.started", "image.started"}:' in broker
    assert 'diagnostics.get("worker_content_dispatch_ack_v1") is True' in broker
    assert "state.worker_content_ack_mono = state.worker_content_ack_mono or now" in broker
    assert "state.browser_started_mono = state.browser_started_mono or now" in broker



def test_worker_dispatch_reports_queue_route_content_readiness_and_ack_stages() -> None:
    background = (ROOT / "chrome_extension/background.js").read_text(encoding="utf-8")
    conversation = (ROOT / "chrome_extension/conversation_dispatch.js").read_text(encoding="utf-8")

    # Browser-side queue and route allocation happen before background.js
    # begins the tab handoff; keep them separate from the content ACK.
    assert "dispatchEnqueuedAt = isRoutedRequest ? monotonicNow() : null" in conversation
    assert "worker_dispatch_queue_wait_ms" in conversation
    assert "worker_route_resolve_ms" in conversation
    assert "message.__chat2api_worker_route_resolve_ms = elapsedMs(routeStarted)" in conversation

    # The background stage clock isolates tab lookup, content injection/ping,
    # and the exact tabs.sendMessage acceptance round trip.
    for key in (
        "worker_target_tab_lookup_ms",
        "worker_ensure_content_ms",
        "worker_tabs_send_message_ms",
        "worker_tab_handler_to_content_ack_ms",
        "worker_dispatch_failure_stage",
    ):
        assert key in background
    assert "stageStarted = performance.now()" in background
    assert 'response.controller === "request-v6" && !response.reroute_pending' in background
    assert "worker_content_dispatch_ack_source: \"background-tabs-sendMessage-request-v6\"" in background


def test_worker_event_ingress_requires_exact_request_client_and_socket() -> None:
    dispatched_socket = object()
    replacement_socket = object()
    state = RequestState(
        request_id="req_r06_owned", client_id="ext_r06",
        dispatch_socket=dispatched_socket,
    )

    assert state.owns_dispatch_socket("ext_r06", dispatched_socket, dispatched_socket)
    assert not state.owns_dispatch_socket("ext_other", dispatched_socket, dispatched_socket)
    assert not state.owns_dispatch_socket("ext_r06", replacement_socket, replacement_socket)
    assert not state.owns_dispatch_socket("ext_r06", dispatched_socket, replacement_socket)

    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    ingress = source[source.index('if request_id and message_type in {'):]
    ownership_gate = ingress.index("active_state.owns_dispatch_socket(")
    broker_publish = ingress.index("await broker.publish(request_id, message)")
    assert ownership_gate < broker_publish



def test_worker_content_ack_requires_the_fixed_request_v6_dispatch_source() -> None:
    async def exercise() -> None:
        broker = RequestBroker()
        state = await broker.create("req_r06_ack_source", "ext_r06")
        forged = await broker.publish("req_r06_ack_source", {
            "type": "chat.diagnostics",
            "request_id": "req_r06_ack_source",
            "diagnostics": {
                "worker_content_dispatch_ack_v1": True,
                "worker_content_dispatch_ack_source": "arbitrary-worker-event",
            },
        })
        assert forged is True
        assert state.worker_content_ack_mono is None

        accepted = await broker.publish("req_r06_ack_source", {
            "type": "chat.diagnostics",
            "request_id": "req_r06_ack_source",
            "diagnostics": {
                "worker_content_dispatch_ack_v1": True,
                "worker_content_dispatch_ack_source": "background-tabs-sendMessage-request-v6",
            },
        })
        assert accepted is True
        assert state.worker_content_ack_mono is not None
        await broker.release("req_r06_ack_source")

    asyncio.run(exercise())


def test_registry_send_binds_the_exact_socket_for_every_broker_request_family(tmp_path) -> None:
    class FakeSocket:
        def __init__(self) -> None:
            self.sent = []

        async def send_json(self, payload) -> None:
            self.sent.append(payload)

    async def exercise() -> None:
        registry = ClientRegistry(tmp_path)
        socket = FakeSocket()
        registry.clients["ext_r06"] = SimpleNamespace(connection_enabled=True)
        registry.sockets["ext_r06"] = socket
        registry.chatgpt_routing_ready = lambda _client_id: True
        request_types = (
            "chat.request", "image.request", "voice.request",
            "voice.live.start", "voice.live.request", "dictation.request",
        )
        states = {
            f"req_r06_{index}": SimpleNamespace(
                dispatch_socket=None,
                dispatch_socket_lock_wait_started_mono=None,
                dispatch_socket_lock_acquired_mono=None,
                dispatch_send_started_mono=None,
                dispatch_send_completed_mono=None,
            )
            for index, _ in enumerate(request_types)
        }
        registry.request_broker = SimpleNamespace(requests=states)

        for index, request_type in enumerate(request_types):
            await registry.send("ext_r06", {
                "type": request_type, "request_id": f"req_r06_{index}",
            })

        assert all(state.dispatch_socket is socket for state in states.values())
        assert all(state.dispatch_socket_lock_wait_started_mono is not None for state in states.values())
        assert all(state.dispatch_socket_lock_acquired_mono is not None for state in states.values())
        assert all(
            state.dispatch_socket_lock_acquired_mono >= state.dispatch_socket_lock_wait_started_mono
            for state in states.values()
        )
        assert all(state.dispatch_send_started_mono is not None for state in states.values())
        assert all(
            state.dispatch_send_completed_mono is not None
            and state.dispatch_send_completed_mono >= state.dispatch_send_started_mono
            for state in states.values()
        )
        assert [payload["type"] for payload in socket.sent] == list(request_types)

    asyncio.run(exercise())


def test_every_request_scoped_worker_sidecar_uses_dispatch_socket_ownership() -> None:
    source = (ROOT / "app/main.py").read_text(encoding="utf-8")
    # File chunks, native DOM evidence, native sidecars, probe events and the
    # ordinary chat/image progress+terminal ingress must share the same
    # request/client/original-socket/current-socket ownership predicate.
    assert source.count("owns_dispatch_socket(") == 5


def test_request_v6_reports_browser_readiness_stages_after_content_ack() -> None:
    source = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")

    assert "async function runDispatchStage(active, stage, task)" in source
    assert 'await runDispatchStage(active, "write_prompt", async () => {' in source
    assert "await writePrompt(active, prompt);" in source
    assert "request_v6_dispatch_stage_evidence_v1" in source
    assert "request_v6_dispatch_to_response_started_ms" in source
    assert "request_v6_dispatch_submit_click_elapsed_ms" in source

    run_request = source[source.index("async function runRequest(message)"):]
    interruption = run_request.index('"resolve_blocking_interruption"')
    prompt_write = run_request.index('"write_prompt"')
    submit = run_request.index('"submit_and_confirm"')
    monitor = run_request.index("await monitor(active)")
    assert interruption < prompt_write < submit < monitor

    started = source[source.index("async function ensureResponseStarted("):
                     source.index("function completeResponsesBridgeDocument(")]
    assert "dispatchElapsedMs(active)" in started
    assert "active.dispatchStageEvidence" in started

    failure = run_request[run_request.index("} catch (error) {"):
                          run_request.index("} finally {")]
    assert "request_v6_dispatch_elapsed_ms" in failure
    assert "request_v6_dispatch_stage_evidence_v1" in failure

def test_request_v6_readiness_continues_when_diagnostic_background_ack_hangs() -> None:
    source = (ROOT / "chrome_extension/content_request_v6.js").read_text(encoding="utf-8")
    request_owner = source[source.index("async function runRequest(message)"):]
    assert 'void diagnostic(active, "dispatch-started", {' in request_owner
    assert 'await diagnostic(active, "dispatch-started"' not in request_owner

    result = subprocess.run(
        ["node", "tests/r06_nonblocking_dispatch_probe.mjs"],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "nonblocking browser dispatch diagnostics: pass" in result.stdout



def test_registry_dispatch_timing_is_not_reset_by_followup_control_messages(tmp_path) -> None:
    class FakeSocket:
        async def send_json(self, _payload) -> None:
            return None

    async def exercise() -> None:
        registry = ClientRegistry(tmp_path)
        socket = FakeSocket()
        registry.clients["ext_r06"] = SimpleNamespace(connection_enabled=True)
        registry.sockets["ext_r06"] = socket
        registry.chatgpt_routing_ready = lambda _client_id: True
        state = SimpleNamespace(
            dispatch_socket=None,
            dispatch_send_started_mono=None,
            dispatch_send_completed_mono=None,
        )
        registry.request_broker = SimpleNamespace(requests={"req_r06_once": state})
        await registry.send("ext_r06", {"type": "chat.request", "request_id": "req_r06_once"})
        original_started = state.dispatch_send_started_mono
        original_completed = state.dispatch_send_completed_mono
        await registry.send("ext_r06", {"type": "chat.cancel", "request_id": "req_r06_once"})
        assert state.dispatch_send_started_mono == original_started
        assert state.dispatch_send_completed_mono == original_completed

    asyncio.run(exercise())



def test_registry_measures_socket_lock_wait_separately_from_send(tmp_path) -> None:
    class FakeSocket:
        async def send_json(self, _payload) -> None:
            return None

    async def exercise() -> None:
        registry = ClientRegistry(tmp_path)
        socket = FakeSocket()
        registry.clients["ext_r06"] = SimpleNamespace(connection_enabled=True)
        registry.sockets["ext_r06"] = socket
        registry.chatgpt_routing_ready = lambda _client_id: True
        lock = asyncio.Lock()
        await lock.acquire()
        registry.socket_locks["ext_r06"] = lock
        state = SimpleNamespace(
            dispatch_socket=None,
            dispatch_socket_lock_wait_started_mono=None,
            dispatch_socket_lock_acquired_mono=None,
            dispatch_send_started_mono=None,
            dispatch_send_completed_mono=None,
        )
        registry.request_broker = SimpleNamespace(requests={"req_r06_lock_wait": state})
        task = asyncio.create_task(registry.send(
            "ext_r06", {"type": "chat.request", "request_id": "req_r06_lock_wait"},
        ))
        await asyncio.sleep(0)
        assert state.dispatch_socket_lock_wait_started_mono is not None
        assert state.dispatch_socket_lock_acquired_mono is None
        lock.release()
        await task
        assert state.dispatch_socket_lock_acquired_mono >= state.dispatch_socket_lock_wait_started_mono
        assert state.dispatch_send_started_mono >= state.dispatch_socket_lock_acquired_mono
        assert state.dispatch_send_completed_mono >= state.dispatch_send_started_mono

    asyncio.run(exercise())


def test_broker_preserves_bounded_worker_dispatch_failure_diagnostics() -> None:
    async def exercise() -> None:
        broker = RequestBroker()
        state = await broker.create("req_r06_failure_evidence", "ext_r06")
        accepted = await broker.publish("req_r06_failure_evidence", {
            "type": "chat.error",
            "request_id": "req_r06_failure_evidence",
            "error": "Content script readiness timed out",
            "diagnostics": {
                "worker_dispatch_queue_wait_ms": 160000.26,
                "worker_route_resolve_ms": 12.34,
                "worker_target_tab_lookup_ms": 4,
                "worker_ensure_content_ms": 900001,
                "worker_tabs_send_message_ms": -1,
                "worker_dispatch_failure_stage": "ensure-content",
                "route_failure_stage": "tab-dispatch",
                "route_failure_code": "routed_tab_disappeared_before_ack",
                "routed_dispatch_terminal_v58": True,
                "route_retired_by_authority_v30": True,
                "rate_limit_terminal": False,
                "retry_after_ms": 2500,
                "request_controller": "request-v6",
                "request_v6_dispatch_timing_version": 1,
                "request_v6_dispatch_elapsed_ms": 160123.46,
                "request_v6_dispatch_stage_evidence_v1": {
                    "write_prompt": {
                        "status": "completed",
                        "started_elapsed_ms": 10,
                        "duration_ms": 25.5,
                        "completed_elapsed_ms": 35.5,
                    },
                    "submit_and_confirm": {
                        "status": "failed",
                        "started_elapsed_ms": 36,
                        "duration_ms": 6000,
                        "completed_elapsed_ms": 6036,
                        "error_type": "TimeoutError",
                        "raw_page_text": "must not be persisted",
                    },
                    "untrusted_stage": {
                        "status": "failed",
                        "duration_ms": 1,
                    },
                    "malformed_stage": {
                        "status": [],
                        "duration_ms": 1,
                    },
                },
                "raw_page_text": "must not be persisted",
                "secret": "must not be persisted",
            },
        })

        assert accepted is True
        assert state.terminal_event_type == "chat.error"
        assert state.diagnostics["worker_dispatch_queue_wait_ms"] == 160000.3
        assert state.diagnostics["worker_route_resolve_ms"] == 12.3
        assert "worker_ensure_content_ms" not in state.diagnostics
        assert "worker_tabs_send_message_ms" not in state.diagnostics
        assert state.diagnostics["worker_dispatch_failure_stage"] == "ensure-content"
        assert state.diagnostics["route_failure_stage"] == "tab-dispatch"
        assert state.diagnostics["route_failure_code"] == "routed_tab_disappeared_before_ack"
        assert state.diagnostics["routed_dispatch_terminal_v58"] is True
        assert state.diagnostics["route_retired_by_authority_v30"] is True
        assert state.diagnostics["rate_limit_terminal"] is False
        assert state.diagnostics["retry_after_ms"] == 2500.0
        assert state.diagnostics["request_v6_dispatch_elapsed_ms"] == 160123.5
        evidence = state.diagnostics["request_v6_dispatch_stage_evidence_v1"]
        assert evidence["submit_and_confirm"]["status"] == "failed"
        assert evidence["submit_and_confirm"]["error_type"] == "TimeoutError"
        assert "raw_page_text" not in evidence["submit_and_confirm"]
        assert "untrusted_stage" not in evidence
        assert "raw_page_text" not in state.diagnostics
        assert "secret" not in state.diagnostics
        await broker.release("req_r06_failure_evidence")

    asyncio.run(exercise())
