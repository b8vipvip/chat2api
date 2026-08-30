from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import FastAPI

from .capacity_queue_v57_patch import install_capacity_queue_v57_patch
from .server_worker_sync_patch import install_server_worker_sync_patch as _install_server_worker_sync_patch


PATCH_ID = "server-worker-auto-sync-starlette-lifespan-v2"


async def _run_handler(handler: Callable[[], Any]) -> None:
    result = handler()
    if inspect.isawaitable(result):
        await result


def _install_final_capacity(app: FastAPI) -> FastAPI:
    return install_capacity_queue_v57_patch(app)


def install_server_worker_sync_patch(app: FastAPI) -> FastAPI:
    """Install Worker sync plus the final v57 admission/settings owner.

    This module is the last server patch installed by entry.py, which makes it the
    correct ownership boundary for replacing historical free-account clamping and
    the older coupling between API concurrency and reserve-window count.
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
