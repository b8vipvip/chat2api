from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import Any

from fastapi import FastAPI


DISPATCH_ACK_TIMEOUT_SECONDS = 45.0
SUBMIT_ACK_TIMEOUT_SECONDS = 120.0
POST_SUBMIT_START_TIMEOUT_SECONDS = 75.0
ABSOLUTE_REQUEST_TIMEOUT_GRACE_SECONDS = 15.0
ORPHAN_RELEASE_GRACE_SECONDS = 2.0
PATCH_ID = "request-stall-v38"
logger = logging.getLogger("chat2api.request_lifecycle")

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


def _stage(state: Any) -> str:
    if getattr(state, "completed_mono", None):
        return "terminal"
    if getattr(state, "_chat2api_generation_started", False):
        return "generation_started"
    if getattr(state, "_chat2api_submit_ack", False):
        return "submitted_waiting_generation"
    if getattr(state, "_chat2api_extension_dispatch_ack", False):
        return "dispatched_waiting_submission"
    return "waiting_dispatch"


def install_request_stall_patch(app: FastAPI) -> FastAPI:
    """Bound browser stalls and guarantee stale request capacity is reclaimed.

    The original v34 guard bounded dispatch and prompt submission only. Production
    diagnostics showed that a request could still occupy Broker capacity after the
    browser had returned to a fully idle reserve pool when the post-submit terminal
    event was lost. This revision adds a post-submit generation-start watchdog, an
    absolute request lease aligned with the API timeout, forced orphan release, and
    lifecycle diagnostics without logging prompts or credentials.
    """

    broker = app.state.broker
    registry = app.state.registry

    if getattr(broker, "_chat2api_request_stall_v38", False):
        return app

    base_create = broker.create
    base_release = broker.release
    base_publish = broker.publish
    base_send = registry.send
    base_attach = registry.attach
    base_detach = registry.detach
    base_summaries = getattr(registry, "summaries", None)

    def _active_request_details(client_id: str) -> list[dict[str, Any]]:
        now = time.perf_counter()
        active = getattr(broker, "client_active_requests", {}).get(str(client_id), {})
        request_ids = list(active) if isinstance(active, dict) else []
        result: list[dict[str, Any]] = []
        for request_id in request_ids:
            state = broker.requests.get(str(request_id))
            if state is None:
                continue
            last_event_mono = float(getattr(state, "_chat2api_last_browser_event_mono", state.created_mono) or state.created_mono)
            hard_deadline = getattr(state, "_chat2api_hard_deadline_mono", None)
            result.append(
                {
                    "request_id": str(state.request_id),
                    "age_seconds": round(max(0.0, now - state.created_mono), 1),
                    "stage": _stage(state),
                    "last_event_type": str(getattr(state, "_chat2api_last_browser_event_type", "") or ""),
                    "last_event_age_seconds": round(max(0.0, now - last_event_mono), 1),
                    "timeout_seconds": getattr(state, "_chat2api_request_timeout_seconds", None),
                    "deadline_remaining_seconds": (
                        round(max(0.0, float(hard_deadline) - now), 1)
                        if hard_deadline is not None else None
                    ),
                }
            )
        return result

    broker.active_request_details = _active_request_details

    if callable(base_summaries):
        def summaries_with_request_lifecycle() -> list[dict[str, Any]]:
            rows = base_summaries()
            for row in rows:
                client_id = str(row.get("client_id") or "")
                details = _active_request_details(client_id)
                row["active_request_details"] = details
                row["active_api_calls"] = len(details)
            return rows

        registry.summaries = summaries_with_request_lifecycle

    async def _force_release_after_grace(state: Any, reason: str) -> None:
        try:
            await asyncio.sleep(ORPHAN_RELEASE_GRACE_SECONDS)
            if broker.requests.get(state.request_id) is not state:
                return
            current = asyncio.current_task()
            tasks: list[asyncio.Task] = []
            for name in (
                "_chat2api_dispatch_watchdog_task",
                "_chat2api_submit_watchdog_task",
                "_chat2api_post_submit_watchdog_task",
                "_chat2api_absolute_watchdog_task",
            ):
                task = getattr(state, name, None)
                if isinstance(task, asyncio.Task) and task is not current and not task.done():
                    task.cancel()
                    tasks.append(task)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await base_release(state.request_id)
            logger.warning(
                "Broker orphan request force-released request_id=%s client=%s reason=%s age_ms=%.1f",
                state.request_id,
                state.client_id,
                reason,
                (time.perf_counter() - state.created_mono) * 1000,
                extra={
                    "request_id": state.request_id,
                    "client_id": state.client_id,
                    "stage": _stage(state),
                    "release_reason": reason,
                },
            )
        except asyncio.CancelledError:
            return

    def _schedule_force_release(state: Any, reason: str) -> None:
        task = getattr(state, "_chat2api_orphan_release_task", None)
        if isinstance(task, asyncio.Task) and not task.done():
            return
        state._chat2api_orphan_release_task = asyncio.create_task(_force_release_after_grace(state, reason))

    async def _best_effort_cancel(state: Any) -> None:
        try:
            await base_send(
                state.client_id,
                {"type": "chat.cancel", "request_id": state.request_id},
            )
        except Exception:
            pass

    async def _fail_and_reclaim(state: Any, *, code: str, message: str, diagnostics: dict[str, Any]) -> None:
        if broker.requests.get(state.request_id) is not state:
            return
        future = getattr(state, "final_future", None)
        if future is not None and future.done():
            return
        state.completed_mono = state.completed_mono or time.perf_counter()
        state.diagnostics.update({"request_stall_guard": PATCH_ID, code: True, **diagnostics})
        logger.warning(
            "Request lifecycle watchdog fired request_id=%s client=%s code=%s stage=%s age_ms=%.1f",
            state.request_id,
            state.client_id,
            code,
            _stage(state),
            (time.perf_counter() - state.created_mono) * 1000,
            extra={
                "request_id": state.request_id,
                "client_id": state.client_id,
                "watchdog_code": code,
                "stage": _stage(state),
            },
        )
        await _best_effort_cancel(state)
        await base_publish(
            state.request_id,
            {"type": "chat.error", "request_id": state.request_id, "error": message},
        )
        _schedule_force_release(state, code)

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
            state._chat2api_last_browser_event_mono = now
            state._chat2api_last_browser_event_type = event_type
            if not getattr(state, "_chat2api_extension_dispatch_ack", False):
                state._chat2api_extension_dispatch_ack = True
                state.diagnostics.setdefault(
                    "extension_dispatch_ack_ms",
                    round((now - state.created_mono) * 1000, 1),
                )
                state.diagnostics["request_stall_guard"] = PATCH_ID
                logger.info(
                    "Browser dispatch acknowledged request_id=%s client=%s event=%s",
                    state.request_id,
                    state.client_id,
                    event_type or "unknown",
                    extra={"request_id": state.request_id, "client_id": state.client_id, "event_type": event_type},
                )

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
                state._chat2api_submit_ack_mono = now
                state.diagnostics.setdefault(
                    "extension_submit_ack_ms",
                    round((now - state.created_mono) * 1000, 1),
                )
                state.diagnostics["request_stall_guard"] = PATCH_ID
                submit_event = getattr(state, "_chat2api_submit_ack_event", None)
                if isinstance(submit_event, asyncio.Event):
                    submit_event.set()
                logger.info(
                    "ChatGPT submission confirmed request_id=%s client=%s event=%s",
                    state.request_id,
                    state.client_id,
                    event_type or "diagnostics",
                    extra={"request_id": state.request_id, "client_id": state.client_id, "event_type": event_type},
                )

            generation_started = bool(
                event_type in {"chat.started", "chat.delta", "chat.snapshot", "chat.completed"}
                or (
                    isinstance(diagnostics, dict)
                    and diagnostics.get("generating_observed") is True
                )
            )
            if generation_started and not getattr(state, "_chat2api_generation_started", False):
                state._chat2api_generation_started = True
                state._chat2api_generation_started_mono = now
                state.diagnostics.setdefault(
                    "extension_generation_started_ms",
                    round((now - state.created_mono) * 1000, 1),
                )
                logger.info(
                    "ChatGPT generation started request_id=%s client=%s event=%s",
                    state.request_id,
                    state.client_id,
                    event_type or "diagnostics",
                    extra={"request_id": state.request_id, "client_id": state.client_id, "event_type": event_type},
                )

            if _terminal_event(event_type):
                logger.info(
                    "Browser terminal event request_id=%s client=%s event=%s age_ms=%.1f",
                    state.request_id,
                    state.client_id,
                    event_type,
                    (now - state.created_mono) * 1000,
                    extra={"request_id": state.request_id, "client_id": state.client_id, "event_type": event_type},
                )

        return await base_publish(request_id, event)

    broker.publish = publish_guarded

    async def _dispatch_watchdog(state: Any) -> None:
        try:
            await asyncio.sleep(DISPATCH_ACK_TIMEOUT_SECONDS)
            if getattr(state, "_chat2api_extension_dispatch_ack", False):
                return
            await _fail_and_reclaim(
                state,
                code="extension_dispatch_watchdog_fired",
                diagnostics={"extension_dispatch_ack_timeout_ms": int(DISPATCH_ACK_TIMEOUT_SECONDS * 1000)},
                message=(
                    "Chrome extension accepted the WebSocket request but did not acknowledge "
                    f"browser dispatch within {int(DISPATCH_ACK_TIMEOUT_SECONDS)}s"
                ),
            )
        except asyncio.CancelledError:
            return

    async def _submit_watchdog(state: Any) -> None:
        try:
            await asyncio.sleep(SUBMIT_ACK_TIMEOUT_SECONDS)
            if getattr(state, "_chat2api_submit_ack", False):
                return
            await _fail_and_reclaim(
                state,
                code="extension_submit_watchdog_fired",
                diagnostics={"extension_submit_ack_timeout_ms": int(SUBMIT_ACK_TIMEOUT_SECONDS * 1000)},
                message=(
                    "Chrome extension routed the request but did not confirm ChatGPT submission "
                    f"within {int(SUBMIT_ACK_TIMEOUT_SECONDS)}s"
                ),
            )
        except asyncio.CancelledError:
            return

    async def _post_submit_watchdog(state: Any) -> None:
        try:
            submit_event = getattr(state, "_chat2api_submit_ack_event", None)
            if not isinstance(submit_event, asyncio.Event):
                return
            await submit_event.wait()
            if getattr(state, "_chat2api_generation_started", False):
                return
            submitted_at = float(getattr(state, "_chat2api_submit_ack_mono", time.perf_counter()))
            remaining = POST_SUBMIT_START_TIMEOUT_SECONDS - (time.perf_counter() - submitted_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
            if getattr(state, "_chat2api_generation_started", False):
                return
            await _fail_and_reclaim(
                state,
                code="post_submit_generation_watchdog_fired",
                diagnostics={
                    "post_submit_generation_start_timeout_ms": int(POST_SUBMIT_START_TIMEOUT_SECONDS * 1000),
                    "last_browser_event_type": str(getattr(state, "_chat2api_last_browser_event_type", "") or ""),
                },
                message=(
                    "ChatGPT accepted the prompt but response generation did not start within "
                    f"{int(POST_SUBMIT_START_TIMEOUT_SECONDS)}s"
                ),
            )
        except asyncio.CancelledError:
            return

    async def _absolute_watchdog(state: Any, timeout_seconds: float) -> None:
        try:
            lease_seconds = max(1.0, float(timeout_seconds)) + ABSOLUTE_REQUEST_TIMEOUT_GRACE_SECONDS
            state._chat2api_hard_deadline_mono = time.perf_counter() + lease_seconds
            await asyncio.sleep(lease_seconds)
            await _fail_and_reclaim(
                state,
                code="absolute_request_lease_watchdog_fired",
                diagnostics={
                    "absolute_request_lease_ms": int(lease_seconds * 1000),
                    "request_timeout_seconds": timeout_seconds,
                    "last_browser_event_type": str(getattr(state, "_chat2api_last_browser_event_type", "") or ""),
                },
                message=(
                    "Request exceeded its browser lease after the API timeout and was reclaimed to prevent stale capacity"
                ),
            )
        except asyncio.CancelledError:
            return

    async def send_with_request_lease(client_id: str, payload: dict[str, Any]) -> None:
        value = dict(payload or {})
        if str(value.get("type") or "") == "chat.request":
            request_id = str(value.get("request_id") or "")
            state = broker.requests.get(request_id)
            if state is not None and _is_chat_request(request_id):
                options = value.get("options") if isinstance(value.get("options"), dict) else {}
                try:
                    timeout_seconds = float(options.get("timeout_seconds") or 300)
                except (TypeError, ValueError):
                    timeout_seconds = 300.0
                timeout_seconds = max(1.0, timeout_seconds)
                state._chat2api_request_timeout_seconds = timeout_seconds
                state.diagnostics["request_timeout_seconds"] = timeout_seconds
                task = getattr(state, "_chat2api_absolute_watchdog_task", None)
                if not isinstance(task, asyncio.Task) or task.done():
                    state._chat2api_absolute_watchdog_task = asyncio.create_task(
                        _absolute_watchdog(state, timeout_seconds)
                    )
                logger.info(
                    "Request dispatched to extension request_id=%s client=%s timeout_seconds=%s",
                    state.request_id,
                    state.client_id,
                    timeout_seconds,
                    extra={
                        "request_id": state.request_id,
                        "client_id": state.client_id,
                        "timeout_seconds": timeout_seconds,
                    },
                )
        await base_send(client_id, value)

    registry.send = send_with_request_lease

    async def create_with_watchdogs(request_id: str, client_id: str):
        state = await base_create(request_id, client_id)
        if _is_chat_request(request_id):
            state._chat2api_extension_dispatch_ack = False
            state._chat2api_submit_ack = False
            state._chat2api_generation_started = False
            state._chat2api_last_browser_event_mono = state.created_mono
            state._chat2api_last_browser_event_type = ""
            state._chat2api_submit_ack_event = asyncio.Event()
            state._chat2api_dispatch_watchdog_task = asyncio.create_task(_dispatch_watchdog(state))
            state._chat2api_submit_watchdog_task = asyncio.create_task(_submit_watchdog(state))
            state._chat2api_post_submit_watchdog_task = asyncio.create_task(_post_submit_watchdog(state))
            state.diagnostics["request_stall_guard"] = PATCH_ID
            logger.info(
                "Request accepted request_id=%s client=%s stage=waiting_dispatch",
                state.request_id,
                state.client_id,
                extra={"request_id": state.request_id, "client_id": state.client_id, "stage": "waiting_dispatch"},
            )
        return state

    async def release_with_watchdogs(request_id: str) -> None:
        state = broker.requests.get(str(request_id))
        tasks: list[asyncio.Task] = []
        client_id = str(getattr(state, "client_id", "") or "") if state is not None else ""
        age_ms = (time.perf_counter() - state.created_mono) * 1000 if state is not None else 0.0
        stage = _stage(state) if state is not None else "missing"
        current = asyncio.current_task()
        if state is not None:
            for name in (
                "_chat2api_dispatch_watchdog_task",
                "_chat2api_submit_watchdog_task",
                "_chat2api_post_submit_watchdog_task",
                "_chat2api_absolute_watchdog_task",
                "_chat2api_orphan_release_task",
            ):
                task = getattr(state, name, None)
                if isinstance(task, asyncio.Task) and task is not current and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await base_release(request_id)
        if state is not None:
            snapshot = broker.capacity_snapshot(client_id) if callable(getattr(broker, "capacity_snapshot", None)) else {}
            logger.info(
                "Broker request released request_id=%s client=%s stage=%s age_ms=%.1f capacity_used=%s",
                request_id,
                client_id,
                stage,
                age_ms,
                snapshot.get("used_units") if isinstance(snapshot, dict) else None,
                extra={
                    "request_id": request_id,
                    "client_id": client_id,
                    "stage": stage,
                    "age_ms": round(age_ms, 1),
                    "capacity_used_after": snapshot.get("used_units") if isinstance(snapshot, dict) else None,
                },
            )

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
    broker._chat2api_request_stall_v38 = True
    registry._chat2api_request_stall_v38 = True
    return app
