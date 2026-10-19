from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RequestState:
    request_id: str
    client_id: str
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    final_future: asyncio.Future[str] | None = None
    text: str = ""
    created_mono: float = field(default_factory=time.perf_counter)
    browser_started_mono: float | None = None
    first_token_mono: float | None = None
    completed_mono: float | None = None
    terminal_event_type: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def timings(self) -> dict[str, float | None]:
        end = self.completed_mono or time.perf_counter()
        return {
            "browser_started_ms": round((self.browser_started_mono - self.created_mono) * 1000, 1) if self.browser_started_mono else None,
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
}
_TERMINAL_PROVENANCE_STRINGS = {
    "response_terminal_source", "network_terminal_evidence_source",
    "terminal_integrity_source",
}


def _terminal_provenance(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("diagnostics")
    if not isinstance(raw, dict):
        return {}
    result: dict[str, Any] = {}
    for key in _TERMINAL_PROVENANCE_NUMBERS:
        value = raw.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[key] = value
    for key in _TERMINAL_PROVENANCE_STRINGS:
        value = raw.get(key)
        if isinstance(value, str):
            result[key] = value[:100]
    for key in ("response_terminal_dom_reconciled", "terminal_integrity_upgraded"):
        if isinstance(raw.get(key), bool):
            result[key] = raw[key]
    return result


class RequestBroker:
    def __init__(self) -> None:
        self.requests: dict[str, RequestState] = {}
        self.client_requests: dict[str, str] = {}
        self.lock = asyncio.Lock()

    async def create(self, request_id: str, client_id: str) -> RequestState:
        async with self.lock:
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

    async def publish(self, request_id: str, event: dict[str, Any]) -> bool:
        state = self.requests.get(request_id)
        if not state:
            return False
        event_type = event.get("type")
        if state.terminal_event_type is not None:
            # Ignore EVERY event after the first terminal. In particular a late
            # error must not overturn completion, and a second completion must
            # not enter the stream queue after the first final frame.
            return False
        now = time.perf_counter()
        if event_type in {"chat.diagnostics", "image.diagnostics"}:
            diagnostics = event.get("diagnostics")
            if isinstance(diagnostics, dict):
                state.diagnostics.update(diagnostics)
        elif event_type in {"chat.started", "image.started"}:
            state.browser_started_mono = state.browser_started_mono or now
            diagnostics = event.get("diagnostics")
            if isinstance(diagnostics, dict):
                state.diagnostics.update(diagnostics)
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
            state.terminal_event_type = str(event_type)
            state.completed_mono = now
            message = str(event.get("error") or event.get("reason") or "Request failed")
            if state.final_future and not state.final_future.done():
                state.final_future.set_exception(RuntimeError(message))
        await state.queue.put(event)
        return True
