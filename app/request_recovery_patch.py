from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI


PATCH_ID = "request-recovery-v40"
TERMINAL_RELEASE_GRACE_SECONDS = 10.0
TERMINAL_REAPER_STALE_SECONDS = 30.0
logger = logging.getLogger("chat2api.request_recovery")

_TERMINAL_EVENTS = {
    "chat.completed",
    "chat.error",
    "chat.cancelled",
    "image.completed",
    "image.error",
    "image.cancelled",
}


def install_request_recovery_patch(app: FastAPI) -> FastAPI:
    """Reclaim terminal Broker entries even when the API handler never releases them.

    request-stall-v38 correctly bounds new silent generations, but production
    diagnostics exposed older terminal requests that had already received
    ``chat.error`` and still remained in ``client_active_requests`` for many hours.
    A completed future is therefore not proof that capacity was returned.

    This layer gives terminal browser events an independent release lease. It also
    reaps already-terminal entries before each new admission, so stale capacity is
    recovered without depending on framework startup/lifespan hooks. Normal request
    handlers still own the fast path; these fallbacks only act when the same state
    is still registered after a grace period.
    """

    broker = app.state.broker
    if getattr(broker, "_chat2api_request_recovery_v40", False):
        return app

    base_create = broker.create
    base_publish = broker.publish
    base_release = broker.release

    async def _release_same_state_after(state: Any, delay_seconds: float, reason: str) -> None:
        try:
            await asyncio.sleep(max(0.0, float(delay_seconds)))
            if broker.requests.get(str(state.request_id)) is not state:
                return
            if not getattr(state, "completed_mono", None):
                return
            state.diagnostics["request_recovery_patch"] = PATCH_ID
            state.diagnostics["terminal_force_release_reason"] = reason
            await base_release(str(state.request_id))
            logger.warning(
                "Terminal Broker request force-released request_id=%s client=%s reason=%s age_ms=%.1f",
                state.request_id,
                state.client_id,
                reason,
                (time.perf_counter() - state.created_mono) * 1000,
                extra={
                    "request_id": state.request_id,
                    "client_id": state.client_id,
                    "release_reason": reason,
                },
            )
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception(
                "Terminal Broker request release failed request_id=%s client=%s reason=%s",
                getattr(state, "request_id", ""),
                getattr(state, "client_id", ""),
                reason,
            )

    def _schedule_terminal_release(state: Any, reason: str) -> None:
        task = getattr(state, "_chat2api_terminal_release_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return
        state._chat2api_terminal_release_task = asyncio.create_task(
            _release_same_state_after(state, TERMINAL_RELEASE_GRACE_SECONDS, reason)
        )

    async def publish_with_terminal_recovery(request_id: str, event: dict[str, Any]) -> bool:
        state = broker.requests.get(str(request_id))
        result = await base_publish(request_id, event)
        event_type = str((event or {}).get("type") or "")
        if state is not None and event_type in _TERMINAL_EVENTS:
            # request-stall-v38 may intentionally ignore a stale socket's synthetic
            # disconnect. Only schedule reclamation if the terminal event actually
            # completed this request/future.
            future = getattr(state, "final_future", None)
            terminal = bool(getattr(state, "completed_mono", None)) or bool(
                future is not None and future.done()
            )
            if terminal and broker.requests.get(str(request_id)) is state:
                state.diagnostics["request_recovery_patch"] = PATCH_ID
                state.diagnostics["terminal_release_scheduled"] = True
                _schedule_terminal_release(state, f"terminal-event:{event_type}")
        return result

    async def reap_terminal_requests_once() -> int:
        now = time.perf_counter()
        reclaimed = 0
        for request_id, state in list(getattr(broker, "requests", {}).items()):
            completed = getattr(state, "completed_mono", None)
            if not completed:
                continue
            terminal_age = max(0.0, now - float(completed))
            if terminal_age < TERMINAL_REAPER_STALE_SECONDS:
                continue
            if broker.requests.get(str(request_id)) is not state:
                continue
            state.diagnostics["request_recovery_patch"] = PATCH_ID
            state.diagnostics["terminal_reaper_fired"] = True
            state.diagnostics["terminal_reaper_age_ms"] = round(terminal_age * 1000, 1)
            await base_release(str(request_id))
            reclaimed += 1
            logger.warning(
                "Terminal Broker reaper reclaimed request_id=%s client=%s terminal_age_ms=%.1f",
                request_id,
                getattr(state, "client_id", ""),
                terminal_age * 1000,
                extra={
                    "request_id": str(request_id),
                    "client_id": str(getattr(state, "client_id", "") or ""),
                    "terminal_age_ms": round(terminal_age * 1000, 1),
                },
            )
        return reclaimed

    async def create_with_terminal_recovery(request_id: str, client_id: str):
        # This admission-time sweep also repairs terminal states that predate the
        # patch or whose normal handler cleanup was interrupted. It runs before the
        # capacity gate sees the next request, preventing ghost busy slots from
        # permanently reducing concurrency.
        await reap_terminal_requests_once()
        return await base_create(request_id, client_id)

    broker.create = create_with_terminal_recovery
    broker.publish = publish_with_terminal_recovery
    broker.request_recovery_reap_once = reap_terminal_requests_once
    broker._chat2api_request_recovery_v40 = True
    return app
