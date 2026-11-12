"""Bounded handoff of detached SSE requests to the authoritative Broker terminal."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("chat2api.stream_detached")


async def _publish_timeout_candidate(state: Any, broker: Any, error: str) -> tuple[bool, bool]:
    """Return (accepted, raised); a failed synthetic publish is never a terminal proof."""
    try:
        accepted = await broker.publish(state.request_id, {
            "type": "chat.error", "request_id": state.request_id,
            "error": error,
        })
        return bool(accepted), False
    except Exception as exc:
        state.diagnostics["detached_stream_timeout_publish_error"] = type(exc).__name__
        logger.exception(
            "Detached SSE timeout candidate could not reach Broker request_id=%s",
            state.request_id,
        )
        return False, True


async def settle_detached_stream(
    *, state: Any, broker: Any, finalize: Callable[[str | None], Awaitable[None]],
    release: Callable[[], Awaitable[None]], wait_seconds: float,
) -> None:
    """A lost HTTP stream is NOT a generation terminal; wait for Broker's Future.

    An expired request may be failed only through the same Broker.publish()
    transition that accepts genuine Worker terminals.
    """
    terminal_error: str | None = None
    try:
        future = state.final_future
        if future is not None:
            try:
                await asyncio.wait_for(asyncio.shield(future), timeout=max(1.0, float(wait_seconds)))
            except asyncio.TimeoutError:
                # The Worker can publish its real terminal in the tiny gap
                # between wait_for timing out and our lease-expiry publish.
                # Only a Broker-accepted timeout is an actual timeout. A
                # rejected synthetic error must not claim to have won.
                timeout_error = "Detached SSE: Worker did not reach a terminal within its request lease"
                terminal_before_timeout_candidate = state.terminal_event_type
                timeout_accepted, timeout_publish_raised = await _publish_timeout_candidate(
                    state, broker, timeout_error,
                )
                if timeout_accepted:
                    terminal_error = timeout_error
                    state.diagnostics["detached_stream_terminal_wait_timeout"] = True
                elif state.terminal_event_type is not None and (
                    not timeout_publish_raised or terminal_before_timeout_candidate is not None
                ):
                    # The actual Worker/Broker terminal won the race, whether
                    # completed, error or cancelled; finalization reads that
                    # terminal instead of fabricating an independent result.
                    terminal_error = None
                    state.diagnostics["detached_stream_worker_won_timeout_race"] = True
                else:
                    # No accepted timeout and no proven Worker terminal: keep
                    # the transport outcome unknown rather than false success.
                    terminal_error = timeout_error
            except Exception as exc:
                terminal_error = str(exc)[:500]
        else:
            terminal_error = "Detached SSE: request has no Broker terminal future"
            terminal_before_timeout_candidate = state.terminal_event_type
            timeout_accepted, timeout_publish_raised = await _publish_timeout_candidate(
                state, broker, terminal_error,
            )
            if not timeout_accepted and state.terminal_event_type is not None and (
                not timeout_publish_raised or terminal_before_timeout_candidate is not None
            ):
                # A missing observer Future does not grant this finalizer a
                # second terminal authority. If Broker already owns a real
                # terminal, preserve it even on this degraded path.
                terminal_error = None
                state.diagnostics["detached_stream_worker_won_timeout_race"] = True
        state.diagnostics["api_stream_detached_worker_terminal"] = state.terminal_event_type or "none"
        state.diagnostics["api_stream_detached_settled"] = True
        await finalize(terminal_error)
    except Exception:
        logger.exception("Could not finalize detached SSE request_id=%s", state.request_id)
        raise
    finally:
        await release()
