"""Bounded handoff of detached SSE requests to the authoritative Broker terminal."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger("chat2api.stream_detached")


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
                terminal_error = "Detached SSE: Worker did not reach a terminal within its request lease"
                state.diagnostics["detached_stream_terminal_wait_timeout"] = True
                await broker.publish(state.request_id, {
                    "type": "chat.error", "request_id": state.request_id,
                    "error": terminal_error,
                })
            except Exception as exc:
                terminal_error = str(exc)[:500]
        else:
            terminal_error = "Detached SSE: request has no Broker terminal future"
            await broker.publish(state.request_id, {
                "type": "chat.error", "request_id": state.request_id,
                "error": terminal_error,
            })
        state.diagnostics["api_stream_detached_worker_terminal"] = state.terminal_event_type or "none"
        state.diagnostics["api_stream_detached_settled"] = True
        await finalize(terminal_error)
    except Exception:
        logger.exception("Could not finalize detached SSE request_id=%s", state.request_id)
        raise
    finally:
        await release()
