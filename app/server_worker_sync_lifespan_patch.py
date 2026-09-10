from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI

from .capacity_scheduler_v58 import install_capacity_scheduler_v58
from .server_worker_sync_patch import install_server_worker_sync_patch as _install_server_worker_sync_patch


PATCH_ID = "server-worker-auto-sync-starlette-lifespan-v2"


async def _run_handler(handler: Callable[[], Any]) -> None:
    result = handler()
    if inspect.isawaitable(result):
        await result


def _install_final_capacity(app: FastAPI) -> FastAPI:
    # v58 replaces v57 rather than wrapping it. This module is the final
    # server-side admission owner installed by entry.py.
    return install_capacity_scheduler_v58(app)


def install_server_worker_sync_patch(app: FastAPI) -> FastAPI:
    """Install Worker sync plus the single v58 admission authority.

    Historical account/free/v57 admission layers are not reinstalled here. The
    final broker.create/release owner is v58: one active request per logical API
    key, FIFO waiting on the server, and independent concurrency only across
    distinct API keys.
    """

    if hasattr(app, "add_event_handler"):
        result = _install_server_worker_sync_patch(app)
        return _install_final_capacity(result)

    captured: dict[str, list[Callable[[], Any]]] = {"startup": [], "shutdown": []}

    def capture_event_handler(event_type: str, handler: Callable[[], Any]) -> None:
        if event_type not in captured:
            raise ValueError(f"Unsupported lifespan event: {event_type}")
        captured[event_type].append(handler)

    setattr(app, "add_event_handler", capture_event_handler)
    try:
        result = _install_server_worker_sync_patch(app)
    finally:
        try:
            delattr(app, "add_event_handler")
        except AttributeError:
            pass

    previous_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def composed_lifespan(inner_app: FastAPI):
        async with previous_lifespan(inner_app) as state:
            for handler in captured["startup"]:
                await _run_handler(handler)
            try:
                yield state
            finally:
                for handler in reversed(captured["shutdown"]):
                    await _run_handler(handler)

    app.router.lifespan_context = composed_lifespan
    app.state.server_worker_sync_lifespan_patch = PATCH_ID
    return _install_final_capacity(result)
