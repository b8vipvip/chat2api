from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable

from fastapi import FastAPI

from .server_worker_sync_patch import install_server_worker_sync_patch as _install_server_worker_sync_patch


PATCH_ID = "server-worker-auto-sync-starlette-lifespan-v2"


async def _run_handler(handler: Callable[[], Any]) -> None:
    result = handler()
    if inspect.isawaitable(result):
        await result


def install_server_worker_sync_patch(app: FastAPI) -> FastAPI:
    """Install the Worker sync patch without relying on removed Starlette APIs.

    Starlette 1.x removed ``Starlette.add_event_handler``. The original v1
    coordinator still registers startup/shutdown hooks through that method, so
    capture those registrations and compose them into the router lifespan when
    the method is unavailable. Older FastAPI/Starlette releases keep their native
    behavior unchanged.
    """

    if hasattr(app, "add_event_handler"):
        return _install_server_worker_sync_patch(app)

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
    return result
