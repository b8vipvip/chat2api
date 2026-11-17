from __future__ import annotations

import asyncio
import math
import re
import time
from urllib.parse import urlsplit
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestState:
    request_id: str
    client_id: str
    # Exact WebSocket that received the initial dispatch. A reconnect for the
    # same client_id does not inherit an already-running request.
    dispatch_socket: Any | None = field(default=None, repr=False, compare=False)
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    final_future: asyncio.Future[str] | None = None
    text: str = ""
    created_mono: float = field(default_factory=time.perf_counter)
    # Server-side dispatch clocks only. A successful registry.send is not
    # proof that the Worker acknowledged or started the browser request.
    dispatch_socket_lock_wait_started_mono: float | None = None
    dispatch_socket_lock_acquired_mono: float | None = None
    dispatch_send_started_mono: float | None = None
    dispatch_send_completed_mono: float | None = None
    # Server timestamp of the authenticated Worker's request-v6 content ACK.
    # This is dispatch evidence only; it does not own request finality.
    worker_content_ack_mono: float | None = None
    # chat.started is emitted when the ChatGPT response/generation is first
    # observed. It is NOT a browser/page startup event. Keep the old
    # browser_started_mono field as a compatibility alias for stored callers.
    response_started_mono: float | None = None
    browser_started_mono: float | None = None
    first_token_mono: float | None = None
    completed_mono: float | None = None
    terminal_event_type: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def owns_dispatch_socket(self, client_id: str, websocket: Any, current_socket: Any) -> bool:
        """Require the assigned Worker and exact original/current socket."""
        return bool(
            client_id
            and websocket is not None
            and self.client_id == str(client_id)
            and self.dispatch_socket is websocket
            and current_socket is websocket
        )

    def timings(self) -> dict[str, float | None]:
        end = self.completed_mono or time.perf_counter()
        response_started = self.response_started_mono
        if response_started is None:
            response_started = self.browser_started_mono
        response_started_ms = (
            round((response_started - self.created_mono) * 1000, 1)
            if response_started is not None else None
        )
        return {
            "response_started_ms": response_started_ms,
            "browser_started_ms": response_started_ms,
            # Stage durations are for diagnostics, never Broker finality.
            # Missing/overlapping clocks return unknown, not a made-up zero.
            "request_admission_wait_ms": self.diagnostics.get("request_admission_wait_ms"),
            "server_dispatch_socket_lock_wait_ms": (
                round((self.dispatch_socket_lock_acquired_mono - self.dispatch_socket_lock_wait_started_mono) * 1000, 1)
                if self.dispatch_socket_lock_wait_started_mono is not None
                and self.dispatch_socket_lock_acquired_mono is not None
                and self.dispatch_socket_lock_acquired_mono >= self.dispatch_socket_lock_wait_started_mono else None
            ),
            "server_send_start_after_broker_ms": (
                round((self.dispatch_send_started_mono - self.created_mono) * 1000, 1)
                if self.dispatch_send_started_mono is not None
                and self.dispatch_send_started_mono >= self.created_mono else None
            ),
            "server_dispatch_send_ms": (
                round((self.dispatch_send_completed_mono - self.dispatch_send_started_mono) * 1000, 1)
                if self.dispatch_send_started_mono is not None
                and self.dispatch_send_completed_mono is not None
                and self.dispatch_send_completed_mono >= self.dispatch_send_started_mono else None
            ),
            "server_send_start_to_worker_content_ack_ms": (
                round((self.worker_content_ack_mono - self.dispatch_send_started_mono) * 1000, 1)
                if self.worker_content_ack_mono is not None
                and self.dispatch_send_started_mono is not None
                and self.worker_content_ack_mono >= self.dispatch_send_started_mono else None
            ),
            "server_send_to_worker_content_ack_ms": (
                round((self.worker_content_ack_mono - self.dispatch_send_completed_mono) * 1000, 1)
                if self.worker_content_ack_mono is not None
                and self.dispatch_send_completed_mono is not None
                and self.worker_content_ack_mono >= self.dispatch_send_completed_mono else None
            ),
            "worker_content_ack_to_response_started_ms": (
                round((response_started - self.worker_content_ack_mono) * 1000, 1)
                if response_started is not None
                and self.worker_content_ack_mono is not None
                and response_started >= self.worker_content_ack_mono else None
            ),
            # Deprecated compatibility alias: this is response start, not
            # Chrome/browser process startup.
            "worker_content_ack_to_browser_started_ms": (
                round((response_started - self.worker_content_ack_mono) * 1000, 1)
                if response_started is not None
                and self.worker_content_ack_mono is not None
                and response_started >= self.worker_content_ack_mono else None
            ),
            "server_send_to_response_started_ms": (
                round((response_started - self.dispatch_send_completed_mono) * 1000, 1)
                if response_started is not None
                and self.dispatch_send_completed_mono is not None
                and response_started >= self.dispatch_send_completed_mono else None
            ),
            "server_send_to_browser_started_ms": (
                round((response_started - self.dispatch_send_completed_mono) * 1000, 1)
                if response_started is not None
                and self.dispatch_send_completed_mono is not None
                and response_started >= self.dispatch_send_completed_mono else None
            ),
            "first_token_ms": round((self.first_token_mono - self.created_mono) * 1000, 1) if self.first_token_mono else None,
            "generation_ms": round((end - self.first_token_mono) * 1000, 1) if self.first_token_mono else None,
            "total_ms": round((end - self.created_mono) * 1000, 1),
            "model_selection_ms": self.diagnostics.get("model_selection_ms"),
            "state_detect_ms": self.diagnostics.get("state_detect_ms"),
            "tab_ready_ms": self.diagnostics.get("tab_ready_ms"),
            "routing_ms": self.diagnostics.get("routing_ms"),
            "attachment_prepare_ms": self.diagnostics.get("attachment_prepare_ms"),
        }


def _monotonic_text(current: str, candidate: str) -> tuple[str, str]:
    """Advance one accumulated assistant text only through prefix-compatible growth."""
    before = str(current or "")
    next_text = str(candidate or "")
    if not next_text or next_text == before:
        return before, "unchanged"
    if not before:
        return next_text, "initial"
    if next_text.startswith(before) and len(next_text) > len(before):
        return next_text, "prefix-extension"
    if before.startswith(next_text):
        return before, "stale-prefix"
    return before, "divergent-ignored"


def _terminal_text(accumulated: str, terminal: str) -> tuple[str, str]:
    """Only the controller's terminal event owns the final assistant body.

    Interim snapshots from DOM/SSE observers are progress evidence, not a second
    authority capable of overruling a shorter or rewritten completed response.
    """
    final = str(terminal or "")
    if final:
        return final, "terminal"
    return str(accumulated or ""), "accumulated"


_TERMINAL_PROVENANCE_NUMBERS = {
    "response_terminal_network_chars", "response_terminal_final_chars",
    "response_terminal_stable_ms", "network_stream_chunks", "network_stream_bytes",
    "network_stream_sequence", "terminal_integrity_initial_chars",
    "terminal_integrity_final_chars",
    "terminal_worker_send_attempt",
    # Explicitly bounded request-only native observer diagnostics. These
    # were sent by v638/v639 but discarded by the terminal allowlist, hiding
    # invalid-native-envelope and structural capture failures in request ZIPs.
    "native_capture_revision", "native_structural_max_children", "native_identity_candidate_count",
    "native_message_identity_candidate_count",
    "native_identity_verified_count",
    # Exact request-owned CodeMirror recovery evidence was emitted by v297
    # but dropped here; missing ZIP fields caused false stale-Worker diagnoses.
    "native_code_editor_state_candidates",
    "native_code_editor_state_resolved",
    "native_code_editor_captured",
    "native_editor_probe_scanned", "native_editor_probe_attachments",
    "native_editor_probe_visible_lines", "native_editor_probe_retries",
    "native_editor_react_fiber_hosts", "native_editor_react_candidate_count",
    "native_editor_react_anchor_count", "native_editor_react_recovered",
    "native_editor_react_hook_nodes", "native_editor_react_array_nodes",
    "native_editor_react_document_nodes",
    "native_canonical_code_integrated",
}
_TERMINAL_PROVENANCE_STRINGS = {
    "response_terminal_source", "network_terminal_evidence_source",
    "terminal_integrity_source",
    "terminal_worker_ack_type",
    "native_capture_failure", "native_transport_ack_error",
    "native_structural_failure",
    "native_reply_scope_binding", "native_reply_scope_phase",
    "native_message_identity_reason",
    "native_chatgpt_conversation_url",
    "native_code_editor_state_failure",
    "native_canonical_code_reason",
    "native_editor_prepared_reason",
}


def _terminal_provenance(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("diagnostics")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _TERMINAL_PROVENANCE_NUMBERS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if (key.startswith("native_code_editor_")
                    or key == "native_canonical_code_integrated") and (
                type(value) is not int or not 0 <= value <= 8
            ):
                continue
            if key.startswith("native_editor_react_") and (
                type(value) is not int or not 0 <= value <= 32
            ):
                continue
            if key.startswith("native_editor_probe_") and (
                type(value) is not int
                or not 0 <= value <= (16 if key == "native_editor_probe_retries" else 4096)
            ):
                continue
            if key == "native_message_identity_candidate_count" and (
                type(value) is not int or not 0 <= value <= 32
            ):
                continue
            result[key] = value
    for key in _TERMINAL_PROVENANCE_STRINGS:
        value = raw.get(key)
        if isinstance(value, str):
            if key == "native_message_identity_reason" and value not in {
                "helper-unavailable", "preconditions", "no-new-assistant-id",
                "ambiguous-new-assistant-ids", "candidate-id-mismatch",
                "verified-native-message-id", "unknown",
            }:
                continue
            if key == "native_chatgpt_conversation_url":
                try:
                    p=urlsplit(value)
                    if (p.scheme != "https" or p.hostname not in
                        {"chatgpt.com", "www.chatgpt.com", "chat.openai.com"} or
                        p.port not in (None,443) or p.username or p.password or
                        p.query or p.fragment or not re.fullmatch(
                            r"/c/[A-Za-z0-9-]{12,90}/?",p.path)):
                        continue
                    result[key]="https://"+p.hostname+p.path
                except ValueError:continue
            else:
                result[key] = value[:100]
    for key in ("response_terminal_dom_reconciled", "terminal_integrity_upgraded",
                "native_chunk_complete", "native_final_capture_committed", "native_observer_loaded",
                "native_capture_attempted", "native_reply_scope_verified",
                "native_message_identity_bound", "native_message_id_scope_verified",
                "native_editor_probe_virtualized",
                "native_editor_prepared_ready"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    # Request-v6 emits these *on every completed native request* but its
    # diagnostics were silently stripped by this terminal allowlist. Preserve
    # only finite gate enums and bounded counters; never forward arbitrary
    # Worker/page text, element markup, signed URLs or raw DOM as telemetry.
    allowed_reasons = {
        "verified", "unknown", "preconditions", "candidate-not-verifiable",
        "no-fresh-toolbar", "ambiguous-reply-scopes", "no-safe-reply-scope",
    }
    gate_reason = raw.get("native_scope_gate_reason")
    if isinstance(gate_reason, str) and gate_reason in allowed_reasons:
        result["native_scope_gate_reason"] = gate_reason
    gate_facts = raw.get("native_scope_gate_facts")
    if isinstance(gate_facts, dict):
        safe_facts: dict[str, Any] = {}
        fact_reason = gate_facts.get("reason")
        if isinstance(fact_reason, str) and fact_reason in allowed_reasons:
            safe_facts["reason"] = fact_reason
        phase = gate_facts.get("phase")
        if phase in ("terminal", "progress"):
            safe_facts["phase"] = phase
        kind = gate_facts.get("candidate_kind")
        if kind in {
            "fresh-roleless-rich-answer-v246", "fresh-roleless-compact-div-v248",
            "after-current-user-turn", "new-turn-identity", "historical-turn", "other",
        }:
            safe_facts["candidate_kind"] = kind
        for key in ("fresh_action_count", "eligible_scope_count"):
            number = gate_facts.get(key)
            if type(number) is int and 0 <= number <= 32:
                safe_facts[key] = number
        if isinstance(gate_facts.get("roleless_reply_verified"), bool):
            safe_facts["roleless_reply_verified"] = gate_facts["roleless_reply_verified"]
        if safe_facts:
            result["native_scope_gate_facts"] = safe_facts
    # Native identity rejection counters: allowlisted, bounded, purely
    # structural. Never accept page text, Message IDs, URLs or raw HTML.
    identity_facts = raw.get("native_message_identity_facts")
    if isinstance(identity_facts, dict):
        safe_identity: dict[str, int] = {}
        for key in (
            "selection_nodes", "historical_nodes", "fresh_nodes",
            "invalid_id_nodes", "hidden_fresh", "bad_wrapper",
            "missing_assistant_heading", "missing_markdown",
            "user_contaminated", "dil_mismatch", "outside_main",
            "eligible_fresh", "historical_heading",
            "rich_controls_inside", "scan_truncated",
        ):
            value = identity_facts.get(key)
            if type(value) is int and 0 <= value <= 32:
                safe_identity[key] = value
        if safe_identity:
            result["native_message_identity_facts"] = safe_identity

    # Successful chat terminals need the same bounded request-v6 timing
    # evidence as failure terminals. Otherwise the terminal allowlist silently
    # drops the final stage snapshot after the Broker closes the request.
    for key in (
        "request_v6_dispatch_elapsed_ms",
        "request_v6_dispatch_submit_click_elapsed_ms",
        "request_v6_dispatch_to_response_started_ms",
    ):
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                numeric = float(value)
            except (OverflowError, ValueError):
                continue
            if math.isfinite(numeric) and 0 <= numeric <= 900000:
                result[key] = round(numeric, 1)
    if type(raw.get("request_v6_dispatch_timing_version")) is int and raw.get("request_v6_dispatch_timing_version") == 1:
        result["request_v6_dispatch_timing_version"] = 1
    evidence = raw.get("request_v6_dispatch_stage_evidence_v1")
    if isinstance(evidence, dict):
        safe_evidence: dict[str, dict[str, Any]] = {}
        for stage_name in _REQUEST_V6_DISPATCH_STAGES:
            safe_entry = _safe_request_v6_dispatch_stage_entry(evidence.get(stage_name))
            if safe_entry is not None:
                safe_evidence[stage_name] = safe_entry
        if safe_evidence:
            result["request_v6_dispatch_stage_evidence_v1"] = safe_evidence
    return result


_WORKER_DISPATCH_FAILURE_TIMINGS = (
    "worker_dispatch_queue_wait_ms",
    "worker_route_resolve_ms",
    "worker_target_tab_lookup_ms",
    "worker_ensure_content_ms",
    "worker_tabs_send_message_ms",
    "worker_tab_handler_to_content_ack_ms",
    "worker_tab_handler_to_failure_ms",
    "request_v6_dispatch_elapsed_ms",
    "request_v6_dispatch_submit_click_elapsed_ms",
    "request_v6_dispatch_to_response_started_ms",
    "retry_after_ms",
)
_WORKER_DISPATCH_FAILURE_STAGES = frozenset({
    "target-tab-lookup", "ensure-content", "content-script-message",
})
_REQUEST_V6_DISPATCH_STAGES = (
    "resolve_blocking_interruption", "prepare_attachments", "write_prompt", "submit_and_confirm",
)


def _safe_request_v6_dispatch_stage_entry(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    status = value.get("status")
    if not isinstance(status, str) or status not in {"started", "completed", "failed", "skipped"}:
        return None
    result: dict[str, Any] = {"status": status}
    for key in ("started_elapsed_ms", "duration_ms", "completed_elapsed_ms"):
        number = value.get(key)
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            try:
                numeric = float(number)
            except (OverflowError, ValueError):
                continue
            if math.isfinite(numeric) and 0 <= numeric <= 900000:
                result[key] = round(numeric, 1)
    error_type = value.get("error_type")
    if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,59}", error_type):
        result["error_type"] = error_type
    return result


def _merge_request_v6_dispatch_diagnostics(
    target: dict[str, Any], incoming: dict[str, Any],
) -> None:
    """Merge asynchronous stage snapshots without allowing stale snapshots to regress them."""
    evidence_key = "request_v6_dispatch_stage_evidence_v1"
    elapsed_key = "request_v6_dispatch_elapsed_ms"
    incoming_evidence = incoming.get(evidence_key)
    incoming_elapsed = incoming.get(elapsed_key)
    update = dict(incoming)
    update.pop(evidence_key, None)
    update.pop(elapsed_key, None)
    target.update(update)

    if isinstance(incoming_evidence, dict):
        current_evidence = target.get(evidence_key)
        merged: dict[str, dict[str, Any]] = {}
        if isinstance(current_evidence, dict):
            for stage_name in _REQUEST_V6_DISPATCH_STAGES:
                safe = _safe_request_v6_dispatch_stage_entry(current_evidence.get(stage_name))
                if safe is not None:
                    merged[stage_name] = safe
        for stage_name in _REQUEST_V6_DISPATCH_STAGES:
            next_entry = _safe_request_v6_dispatch_stage_entry(incoming_evidence.get(stage_name))
            if next_entry is None:
                continue
            previous = merged.get(stage_name)
            previous_status = previous.get("status") if previous else None
            next_status = next_entry["status"]
            terminal_statuses = {"completed", "failed", "skipped"}
            if previous_status in terminal_statuses:
                # Each request-v6 stage runs once. Once it reaches a terminal
                # state, a later snapshot may refine timing for that SAME
                # terminal state, but must never flip completed/failed/skipped
                # merely because its timestamp is larger or equal.
                if next_status != previous_status:
                    continue
                previous_elapsed = previous.get("completed_elapsed_ms")
                next_elapsed = next_entry.get("completed_elapsed_ms")
                if previous_elapsed is not None and next_elapsed is None:
                    continue
                if (isinstance(previous_elapsed, (int, float))
                        and isinstance(next_elapsed, (int, float))
                        and next_elapsed <= previous_elapsed):
                    continue
                if previous_elapsed is None and next_elapsed is None:
                    continue
            merged[stage_name] = next_entry
        if merged:
            target[evidence_key] = merged

    if isinstance(incoming_elapsed, (int, float)) and not isinstance(incoming_elapsed, bool):
        try:
            numeric_elapsed = float(incoming_elapsed)
        except (OverflowError, ValueError):
            numeric_elapsed = -1
        if math.isfinite(numeric_elapsed) and 0 <= numeric_elapsed <= 900000:
            previous_elapsed = target.get(elapsed_key)
            if isinstance(previous_elapsed, (int, float)) and not isinstance(previous_elapsed, bool):
                try:
                    previous_numeric = float(previous_elapsed)
                except (OverflowError, ValueError):
                    previous_numeric = -1
                if math.isfinite(previous_numeric) and 0 <= previous_numeric <= 900000:
                    numeric_elapsed = max(numeric_elapsed, previous_numeric)
            target[elapsed_key] = round(numeric_elapsed, 1)


def _worker_dispatch_failure_provenance(event: dict[str, Any]) -> dict[str, Any]:
    """Keep bounded dispatch diagnostics from terminal errors, never raw page data."""
    raw = event.get("diagnostics")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _WORKER_DISPATCH_FAILURE_TIMINGS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                numeric = float(value)
            except (OverflowError, ValueError):
                continue
            if math.isfinite(numeric) and 0 <= numeric <= 900000:
                result[key] = round(numeric, 1)
    stage = raw.get("worker_dispatch_failure_stage")
    if isinstance(stage, str) and stage in _WORKER_DISPATCH_FAILURE_STAGES:
        result["worker_dispatch_failure_stage"] = stage
    # Routed Worker allocation failures use the same terminal event but a
    # separate route-stage vocabulary from background.js tab handoff errors.
    for key in ("route_failure_stage", "route_failure_code"):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,60}", value):
            result[key] = value
    for key in ("routed_dispatch_terminal_v58", "route_retired_by_authority_v30", "rate_limit_terminal"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    if raw.get("request_controller") == "request-v6":
        result["request_controller"] = "request-v6"
    if type(raw.get("request_v6_dispatch_timing_version")) is int and raw.get("request_v6_dispatch_timing_version") == 1:
        result["request_v6_dispatch_timing_version"] = 1

    evidence = raw.get("request_v6_dispatch_stage_evidence_v1")
    if isinstance(evidence, dict):
        safe_evidence: dict[str, dict[str, Any]] = {}
        # Iterate only the four known stages. Do not scan an untrusted
        # Worker-supplied mapping with an arbitrary number of keys.
        for stage_name in _REQUEST_V6_DISPATCH_STAGES:
            entry = evidence.get(stage_name)
            if not isinstance(entry, dict):
                continue
            status = entry.get("status")
            if not isinstance(status, str) or status not in {"started", "completed", "failed", "skipped"}:
                continue
            safe_entry: dict[str, Any] = {"status": status}
            for key in ("started_elapsed_ms", "duration_ms", "completed_elapsed_ms"):
                value = entry.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    try:
                        numeric = float(value)
                    except (OverflowError, ValueError):
                        continue
                    if math.isfinite(numeric) and 0 <= numeric <= 900000:
                        safe_entry[key] = round(numeric, 1)
            error_type = entry.get("error_type")
            if isinstance(error_type, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,59}", error_type):
                safe_entry["error_type"] = error_type
            safe_evidence[stage_name] = safe_entry
        if safe_evidence:
            result["request_v6_dispatch_stage_evidence_v1"] = safe_evidence
    return result


class RequestBroker:
    def __init__(self) -> None:
        self.requests: dict[str, RequestState] = {}
        self.client_requests: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def create(self, request_id: str, client_id: str) -> RequestState:
        async with self.lock:
            # Request identity is global across Workers. Never replace an
            # existing lease when a caller replays the same ID on another
            # client; that would orphan the original Worker/client mapping.
            if request_id in self.requests:
                raise RuntimeError("The request ID is already active")
            if client_id in self.client_requests:
                raise RuntimeError("The selected extension is busy with another request")
            loop = asyncio.get_running_loop()
            state = RequestState(request_id=request_id, client_id=client_id, final_future=loop.create_future())
            self.requests[request_id] = state
            self.client_requests[client_id] = request_id
            return state

    async def release(self, request_id: str) -> None:
        async with self.lock:
            state = self.requests.pop(request_id, None)
            if state and state.final_future and state.final_future.done() and not state.final_future.cancelled():
                try:
                    state.final_future.exception()
                except asyncio.CancelledError:
                    pass
            if not state:
                return

            active_by_client = getattr(self, "client_active_requests", None)
            if isinstance(active_by_client, dict):
                active = active_by_client.get(state.client_id)
                if isinstance(active, dict):
                    active.pop(request_id, None)
                    if not active:
                        active_by_client.pop(state.client_id, None)

            if self.client_requests.get(state.client_id) == request_id:
                replacement = None
                if isinstance(active_by_client, dict):
                    active = active_by_client.get(state.client_id)
                    if isinstance(active, dict):
                        replacement = next(iter(active), None)
                if replacement:
                    self.client_requests[state.client_id] = replacement
                else:
                    self.client_requests.pop(state.client_id, None)


    async def publish_worker_event(
        self,
        request_id: str,
        event: dict[str, Any],
        *,
        client_id: str,
        websocket: Any,
        current_socket: Any,
    ) -> bool:
        """Publish a Worker event only from the current socket assigned the request.

        Request IDs are correlation identifiers, not Worker credentials. The
        authenticated socket identity and the Broker's request assignment must
        both match before any progress or terminal event can enter the queue.
        """
        if websocket is None or current_socket is not websocket:
            return False
        state = self.requests.get(request_id)
        if state is None or not state.owns_dispatch_socket(
            client_id, websocket, current_socket
        ):
            return False
        return await self.publish(request_id, event)

    async def publish(self, request_id: str, event: dict[str, Any]) -> bool:
        state = self.requests.get(request_id)
        if not state:
            return False
        event_type = event.get("type")
        if state.terminal_event_type is not None or state.completed_mono is not None:
            # Ignore EVERY event after a Worker terminal or a server-owned
            # terminal cutoff (for example, an API timeout). A late Worker
            # completion must not overturn an already finalized API response.
            return False
        now = time.perf_counter()
        if event_type in {"chat.diagnostics", "image.diagnostics"}:
            diagnostics = event.get("diagnostics")
            if isinstance(diagnostics, dict):
                _merge_request_v6_dispatch_diagnostics(state.diagnostics, diagnostics)
                if (event_type == "chat.diagnostics"
                        and diagnostics.get("worker_content_dispatch_ack_v1") is True
                        and diagnostics.get("worker_content_dispatch_ack_source")
                        == "background-tabs-sendMessage-request-v6"):
                    state.worker_content_ack_mono = state.worker_content_ack_mono or now
                    state.diagnostics["worker_content_dispatch_ack_received"] = True
        elif event_type in {"chat.started", "image.started"}:
            state.response_started_mono = state.response_started_mono or now
            state.browser_started_mono = state.browser_started_mono or state.response_started_mono
            diagnostics = event.get("diagnostics")
            if isinstance(diagnostics, dict):
                _merge_request_v6_dispatch_diagnostics(state.diagnostics, diagnostics)
        elif event_type == "chat.delta":
            state.first_token_mono = state.first_token_mono or now
            delta = str(event.get("delta") or "")
            state.text += delta
        elif event_type == "chat.snapshot":
            state.first_token_mono = state.first_token_mono or now
            state.text, snapshot_source = _monotonic_text(state.text, str(event.get("text") or ""))
            state.diagnostics["snapshot_text_source"] = snapshot_source
            state.diagnostics["snapshot_text_chars"] = len(state.text)
        elif event_type == "chat.completed":
            state.terminal_event_type = str(event_type)
            state.completed_mono = now
            state.diagnostics.update(_terminal_provenance(event))
            final, source = _terminal_text(state.text, str(event.get("text") or ""))
            state.text = final
            state.diagnostics["terminal_text_authority"] = "request-controller"
            state.diagnostics["terminal_text_source"] = source
            state.diagnostics["terminal_text_chars"] = len(final)
            if state.final_future and not state.final_future.done():
                state.final_future.set_result(final)
        elif event_type == "image.completed":
            state.terminal_event_type = str(event_type)
            state.completed_mono = now
        elif event_type in {"chat.error", "chat.cancelled", "image.error", "image.cancelled"}:
            # Worker dispatch failures carry the only evidence of which
            # pre-browser stage stalled. Preserve a strict, bounded allowlist
            # before this terminal event closes the request.
            state.diagnostics.update(_worker_dispatch_failure_provenance(event))
            state.terminal_event_type = str(event_type)
            state.completed_mono = now
            message = str(event.get("error") or event.get("reason") or "Request failed")
            if state.final_future and not state.final_future.done():
                state.final_future.set_exception(RuntimeError(message))
        await state.queue.put(event)
        return True
