from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI


PATCH_ID = "worker-transport-recovery-v47"
WORKER_RECONNECT_GRACE_SECONDS = 8.0
logger = logging.getLogger("chat2api.worker_transport")


def _disconnect_event(event: dict[str, Any]) -> bool:
    event_type = str((event or {}).get("type") or "")
    message = str((event or {}).get("error") or (event or {}).get("reason") or "")
    return event_type in {"chat.error", "image.error"} and message in {
        "Chrome extension disconnected",
        "Worker disconnected",
    }


def install_worker_transport_recovery_patch(app: FastAPI) -> FastAPI:
    """Give an in-flight browser request a short Worker reconnect window.

    The legacy WebSocket handler emits a synthetic ``chat.error`` from its
    ``finally`` block whenever a browser transport closes.  On Linux, MV3 service
    worker / proxy reconnects can replace that socket a couple of seconds later
    while the ChatGPT page keeps generating normally.  Treating the first socket
    close as terminal loses the already-submitted request and its eventual page
    result.

    This wrapper delays only that synthetic disconnect.  If a newer socket is
    attached during the grace window, the disconnect is ignored and the existing
    Broker request keeps its original timeout/watchdogs.  A companion browser
    outbox replays a terminal ``chat.completed`` event if completion happened
    while the transport was down.  Real browser errors are untouched.
    """

    broker = app.state.broker
    registry = app.state.registry
    if getattr(broker, "_chat2api_worker_transport_recovery_v47", False):
        return app

    base_publish = broker.publish

    async def publish_with_worker_reconnect(request_id: str, event: dict[str, Any]) -> bool:
        state = broker.requests.get(str(request_id))
        if state is None or not _disconnect_event(event):
            return await base_publish(request_id, event)

        client_id = str(getattr(state, "client_id", "") or "")
        socket_before = registry.sockets.get(client_id)
        started = time.perf_counter()
        state.diagnostics["worker_transport_recovery"] = PATCH_ID
        state.diagnostics["worker_disconnect_grace_ms"] = int(WORKER_RECONNECT_GRACE_SECONDS * 1000)
        state.diagnostics["worker_disconnect_observed"] = True

        logger.warning(
            "Worker transport disconnected during active request; waiting for reconnect request_id=%s worker=%s grace_ms=%s",
            request_id,
            client_id,
            int(WORKER_RECONNECT_GRACE_SECONDS * 1000),
            extra={"request_id": str(request_id), "client_id": client_id},
        )

        try:
            await asyncio.sleep(max(0.0, float(WORKER_RECONNECT_GRACE_SECONDS)))
        except asyncio.CancelledError:
            raise

        # A replayed terminal event may have completed/released the request while
        # the old WebSocket handler was sleeping.  Never overwrite that outcome.
        if broker.requests.get(str(request_id)) is not state:
            return True
        future = getattr(state, "final_future", None)
        if getattr(state, "completed_mono", None) or (future is not None and future.done()):
            return True

        socket_after = registry.sockets.get(client_id)
        if socket_after is not None and socket_after is not socket_before:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            state.diagnostics["worker_transport_reconnected"] = True
            state.diagnostics["worker_transport_reconnect_ms"] = elapsed_ms
            logger.info(
                "Worker transport reconnected; preserving active request request_id=%s worker=%s reconnect_ms=%.1f",
                request_id,
                client_id,
                elapsed_ms,
                extra={"request_id": str(request_id), "client_id": client_id, "reconnect_ms": elapsed_ms},
            )
            return True

        state.diagnostics["worker_transport_reconnected"] = False
        state.diagnostics["worker_disconnect_grace_expired"] = True
        terminal = dict(event or {})
        terminal["error"] = "Worker disconnected"
        return await base_publish(request_id, terminal)

    broker.publish = publish_with_worker_reconnect
    broker._chat2api_worker_transport_recovery_v47 = True
    return app
