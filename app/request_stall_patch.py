from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any

from fastapi import FastAPI


DISPATCH_ACK_TIMEOUT_SECONDS = 45.0
SUBMIT_ACK_TIMEOUT_SECONDS = 120.0
PATCH_ID = "request-stall-v34"

_socket_task_context: contextvars.ContextVar[tuple[str, Any] | None] = contextvars.ContextVar(
    "chat2api_request_stall_socket", default=None
)
_detaching_current_socket: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "chat2api_request_stall_detaching_current", default=False
)


def _is_chat_request(request_id: str) -> bool:
    return str(request_id or "").startswith("req_")


def _terminal_event(event_type: str) -> bool:
    return event_type in {
        "chat.completed", "chat.error", "chat.cancelled",
        "image.completed", "image.error", "image.cancelled",
    }


def install_request_stall_patch(app: FastAPI) -> FastAPI:
    """Bound silent browser stalls and ignore disconnects from replaced sockets.

    The normal request timeout can be several minutes. A live WebSocket that accepts
    a request but whose MV3 dispatch chain never starts used to occupy capacity for
    that entire timeout. This patch adds two bounded acknowledgements for text
    requests while preserving the existing full generation timeout once ChatGPT has
    actually accepted the prompt.
    """

    broker = app.state.broker
    registry = app.state.registry

    if getattr(broker, "_chat2api_request_stall_v34", False):
        return app

    base_create = broker.create
    base_release = broker.release
    base_publish = broker.publish
    base_attach = registry.attach
    base_detach = registry.detach

    async def publish_guarded(request_id: str, event: dict[str, Any]) -> bool:
        state = broker.requests.get(str(request_id))
        event_type = str((event or {}).get("type") or "")

        # When ClientRegistry.attach replaces an older WebSocket, the older FastAPI
        # handler still executes its finally block. Ignore only that stale handler's
        # synthetic disconnect; a genuine current-socket disconnect must still fail
        # all active requests immediately.
        if state and event_type in {"chat.error", "chat.cancelled", "image.error", "image.cancelled"}:
            message = str((event or {}).get("error") or (event or {}).get("reason") or "")
            socket_context = _socket_task_context.get()
            if message == "Chrome extension disconnected" and socket_context:
                context_client_id, context_socket = socket_context
                current_socket = registry.sockets.get(context_client_id)
                if (
                    state.client_id == context_client_id
                    and current_socket is not None
                    and current_socket is not context_socket
                    and not _detaching_current_socket.get()
                ):
                    state.diagnostics["stale_extension_disconnect_ignored"] = True
                    state.diagnostics["request_stall_guard"] = PATCH_ID
                    return True

        if state:
            now = time.perf_counter()
            if not getattr(state, "_chat2api_extension_dispatch_ack", False):
                state._chat2api_extension_dispatch_ack = True
                state.diagnostics.setdefault(
                    "extension_dispatch_ack_ms",
                    round((now - state.created_mono) * 1000, 1),
                )
                state.diagnostics["request_stall_guard"] = PATCH_ID

            diagnostics = (event or {}).get("diagnostics")
            submitted = bool(
                event_type in {"chat.started", "chat.delta", "chat.snapshot", "chat.completed"}
                or _terminal_event(event_type)
                or (
                    isinstance(diagnostics, dict)
                    and (
                        diagnostics.get("submission_confirmed") is True
                        or str(diagnostics.get("submit_stage") or "") == "confirmed"
                    )
                )
            )
            if submitted and not getattr(state, "_chat2api_submit_ack", False):
                state._chat2api_submit_ack = True
                state.diagnostics.setdefault(
                    "extension_submit_ack_ms",
                    round((now - state.created_mono) * 1000, 1),
                )
                state.diagnostics["request_stall_guard"] = PATCH_ID

        return await base_publish(request_id, event)

    broker.publish = publish_guarded

    async def _best_effort_cancel(state) -> None:
        try:
            await registry.send(
                state.client_id,
                {"type": "chat.cancel", "request_id": state.request_id},
            )
        except Exception:
            pass

    async def _dispatch_watchdog(state) -> None:
        try:
            await asyncio.sleep(DISPATCH_ACK_TIMEOUT_SECONDS)
            if broker.requests.get(state.request_id) is not state:
                return
            future = getattr(state, "final_future", None)
            if future is not None and future.done():
                return
            if getattr(state, "_chat2api_extension_dispatch_ack", False):
                return
            state.completed_mono = state.completed_mono or time.perf_counter()
            state.diagnostics.update(
                {
                    "request_stall_guard": PATCH_ID,
                    "extension_dispatch_watchdog_fired": True,
                    "extension_dispatch_ack_timeout_ms": int(DISPATCH_ACK_TIMEOUT_SECONDS * 1000),
                }
            )
            await _best_effort_cancel(state)
            await base_publish(
                state.request_id,
                {
                    "type": "chat.error",
                    "request_id": state.request_id,
                    "error": (
                        "Chrome extension accepted the WebSocket request but did not acknowledge "
                        f"browser dispatch within {int(DISPATCH_ACK_TIMEOUT_SECONDS)}s"
                    ),
                },
            )
        except asyncio.CancelledError:
            return

    async def _submit_watchdog(state) -> None:
        try:
            await asyncio.sleep(SUBMIT_ACK_TIMEOUT_SECONDS)
            if broker.requests.get(state.request_id) is not state:
                return
            future = getattr(state, "final_future", None)
            if future is not None and future.done():
                return
            if getattr(state, "_chat2api_submit_ack", False):
                return
            state.completed_mono = state.completed_mono or time.perf_counter()
            state.diagnostics.update(
                {
                    "request_stall_guard": PATCH_ID,
                    "extension_submit_watchdog_fired": True,
                    "extension_submit_ack_timeout_ms": int(SUBMIT_ACK_TIMEOUT_SECONDS * 1000),
                }
            )
            await _best_effort_cancel(state)
            await base_publish(
                state.request_id,
                {
                    "type": "chat.error",
                    "request_id": state.request_id,
                    "error": (
                        "Chrome extension routed the request but did not confirm ChatGPT submission "
                        f"within {int(SUBMIT_ACK_TIMEOUT_SECONDS)}s"
                    ),
                },
            )
        except asyncio.CancelledError:
            return

    async def create_with_watchdogs(request_id: str, client_id: str):
        state = await base_create(request_id, client_id)
        if _is_chat_request(request_id):
            state._chat2api_extension_dispatch_ack = False
            state._chat2api_submit_ack = False
            state._chat2api_dispatch_watchdog_task = asyncio.create_task(_dispatch_watchdog(state))
            state._chat2api_submit_watchdog_task = asyncio.create_task(_submit_watchdog(state))
        return state

    async def release_with_watchdogs(request_id: str) -> None:
        state = broker.requests.get(str(request_id))
        tasks = []
        if state is not None:
            for name in ("_chat2api_dispatch_watchdog_task", "_chat2api_submit_watchdog_task"):
                task = getattr(state, name, None)
                if isinstance(task, asyncio.Task) and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await base_release(request_id)

    broker.create = create_with_watchdogs
    broker.release = release_with_watchdogs

    async def attach_with_socket_context(client_id: str, websocket) -> None:
        _socket_task_context.set((str(client_id), websocket))
        await base_attach(client_id, websocket)

    async def detach_current_socket_only(client_id: str, websocket) -> None:
        current_socket = registry.sockets.get(str(client_id))
        # A non-null different socket is definitive evidence that this callback is
        # from a replaced connection. None is allowed through because admin delete
        # and other teardown paths may have already removed the current socket.
        if current_socket is not None and current_socket is not websocket:
            return
        token = _detaching_current_socket.set(True)
        try:
            await base_detach(client_id, websocket)
        finally:
            _detaching_current_socket.reset(token)

    registry.attach = attach_with_socket_context
    registry.detach = detach_current_socket_only
    broker._chat2api_request_stall_v34 = True
    registry._chat2api_request_stall_v34 = True
    return app
