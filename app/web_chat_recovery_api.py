"""W-04: owner-scoped snapshot recovery for disconnected Web Chat turns.

This endpoint observes the existing request-v6/Broker/Telemetry authorities.
It never redispatches a generation and never infers completion from transport EOF.
"""
from __future__ import annotations

import asyncio
import json
import re
import time

from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from .admin_auth import same_origin_admin_request
from .user_commerce import USER_SESSION_COOKIE


_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,120}$")
_TERMINAL = {"completed", "error", "cancelled", "stalled"}
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


def _http_error(status_code: int, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail=detail, headers=_PRIVATE_HEADERS)


def _best_partial_text(*values: Any) -> str:
    """Choose the longest bounded unverified candidate without concatenating streams."""
    candidates = [
        value[:50_000] for value in values
        if isinstance(value, str) and value
    ]
    return max(candidates, key=len, default="")


def install_web_chat_recovery_api(app: FastAPI) -> FastAPI:
    if getattr(app.state, "web_chat_recovery_api_installed", False):
        return app
    app.state.web_chat_recovery_api_installed = True

    def current_user(request: Request):
        accounts = getattr(request.app.state, "user_accounts", None)
        if accounts is None:
            _http_error(503, "user account service unavailable")
        user = accounts.authenticate(request.cookies.get(USER_SESSION_COOKIE))
        if not user:
            _http_error(401, "user login required")
        return accounts, user

    @app.get("/api/user/chat/requests/{request_id}/recovery")
    async def recover_chat_request(
        request_id: str, request: Request, response: Response,
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        if not _REQUEST_ID_RE.fullmatch(request_id):
            _http_error(404, "request not found")

        accounts, user = current_user(request)
        telemetry = getattr(request.app.state, "telemetry", None)
        broker = getattr(request.app.state, "broker", None)
        if telemetry is None or broker is None:
            _http_error(503, "request recovery unavailable")

        row = telemetry.get(request_id)
        if row is None:
            _http_error(404, "request not found")
        key_id = str(row.get("api_key_id") or "")
        # Deliberately conceal both unknown and non-owned request IDs.
        if not key_id or not accounts.owns_key(str(user["user_id"]), key_id):
            _http_error(404, "request not found")

        status = str(row.get("status") or "unknown").lower()
        state = broker.requests.get(request_id)
        updated_at = row.get("updated_at") or row.get("recorded_at")
        journal = getattr(request.app.state, "web_chat_event_journal", None)
        journal_snapshot = {}
        if journal is not None:
            try:
                journal_snapshot = await journal.snapshot(request_id, key_id)
            except Exception:
                journal_snapshot = {}
        last_event_id = int(journal_snapshot.get("last_event_id") or 0)

        if status == "completed":
            final_text = str(row.get("response_text") or "")
            if not final_text and journal_snapshot.get("terminal_status") == "completed":
                final_text = str(journal_snapshot.get("delivered_text") or "")
            available = (
                bool(row.get("response_text_available")) and bool(row.get("response_text"))
            ) or bool(final_text and journal_snapshot.get("terminal_status") == "completed")
            return {
                "request_id": request_id,
                "status": "completed",
                "terminal": True,
                "recoverable": False,
                "active": False,
                "response_text": final_text if available else "",
                "response_text_available": available,
                "last_event_id": last_event_id,
                "updated_at": updated_at,
            }

        if status in _TERMINAL:
            # Failed/aborted/stalled candidate text is never promoted to a
            # completed reply. A clearly labelled partial copy is available
            # so the user can retain work after a process restart.
            partial_text = ""
            if status == "stalled":
                partial_text = _best_partial_text(
                    row.get("response_text_provisional"),
                    journal_snapshot.get("delivered_text"),
                    journal_snapshot.get("worker_partial_text"),
                )
            return {
                "request_id": request_id,
                "status": status,
                "terminal": True,
                "recoverable": False,
                "active": False,
                "response_text": "",
                "response_text_available": False,
                "partial_text": partial_text,
                "partial_text_available": bool(partial_text),
                "last_event_id": last_event_id,
                "error_code": "request_" + status,
                "updated_at": updated_at,
            }

        if status == "recovering":
            # A prior server process lost its in-memory Broker lease. Keep the
            # same request open briefly for the extension's durable terminal
            # outbox; never POST or dispatch a replacement generation.
            # Worker deltas and already-delivered chunks are durable even when
            # the Telemetry provisional projection was not written before the
            # process exited. Expose them as explicitly unverified partial text
            # so the reconnecting UI does not appear blank during the grace.
            recovery_partial_text = _best_partial_text(
                row.get("response_text_provisional"),
                journal_snapshot.get("delivered_text"),
                journal_snapshot.get("worker_partial_text"),
            )
            return {
                "request_id": request_id,
                "status": "recovering",
                "terminal": False,
                "recoverable": True,
                "active": True,
                "phase": "reconnecting",
                "retry_after_ms": 500,
                "last_event_id": last_event_id,
                "response_text": "",
                "response_text_available": False,
                "partial_text": recovery_partial_text,
                "partial_text_available": bool(recovery_partial_text),
                "partial_text_unverified": bool(recovery_partial_text),
                "updated_at": updated_at,
            }

        if status == "queued":
            # The request may be waiting for Worker admission before a Broker
            # state exists. Keep polling the same request; never POST it again.
            return {
                "request_id": request_id,
                "status": "queued",
                "terminal": False,
                "recoverable": True,
                "active": True,
                "phase": "admission",
                "response_text": "",
                "response_text_available": False,
                "retry_after_ms": 1000,
                "last_event_id": last_event_id,
                "updated_at": updated_at,
            }

        if status == "running" and state is not None:
            terminal_seen = bool(getattr(state, "terminal_event_type", None))
            return {
                "request_id": request_id,
                "status": "running",
                "terminal": False,
                "recoverable": True,
                "active": True,
                "phase": "finalizing" if terminal_seen else "generating",
                "response_text": "",
                "response_text_available": False,
                "retry_after_ms": 1000,
                "last_event_id": last_event_id,
                "updated_at": updated_at,
            }

        # A running telemetry row with no Broker state is not safe to replay.
        # This can happen after a process restart or retention/lease boundary.
        return {
            "request_id": request_id,
            "status": "unknown",
            "terminal": False,
            "recoverable": False,
            "active": False,
            "error_code": "active_request_state_unavailable",
            "response_text": "",
            "response_text_available": False,
            "updated_at": updated_at,
        }


    @app.get("/api/user/chat/requests/{request_id}/events")
    async def replay_chat_request_events(
        request_id: str,
        request: Request,
        response: Response,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        if not _REQUEST_ID_RE.fullmatch(request_id):
            _http_error(404, "request not found")
        accounts, user = current_user(request)
        telemetry = getattr(request.app.state, "telemetry", None)
        broker = getattr(request.app.state, "broker", None)
        journal = getattr(request.app.state, "web_chat_event_journal", None)
        if telemetry is None or broker is None or journal is None:
            _http_error(503, "request replay unavailable")
        row = telemetry.get(request_id)
        if row is None:
            _http_error(404, "request not found")
        key_id = str(row.get("api_key_id") or "")
        if not key_id or not accounts.owns_key(str(user["user_id"]), key_id):
            _http_error(404, "request not found")

        cursor = int(after)
        if last_event_id is not None and last_event_id.strip():
            try:
                header_cursor = int(last_event_id.strip())
            except ValueError:
                _http_error(400, "invalid Last-Event-ID")
            if header_cursor < 0:
                _http_error(400, "invalid Last-Event-ID")
            cursor = max(cursor, header_cursor)

        start_cursor = cursor

        async def replay():
            cursor = start_cursor
            last_heartbeat = time.monotonic()
            tick = 0
            while True:
                tick += 1
                # This stream may remain open for a long-running generation.
                # Revalidate the HttpOnly session and API-key ownership just as
                # the native-content stream does; a logout, revoked session or
                # detached key must stop further private event delivery.
                if tick % 40 == 0:
                    refreshed = accounts.authenticate(
                        request.cookies.get(USER_SESSION_COOKIE)
                    )
                    if (
                        not refreshed
                        or str(refreshed.get("user_id") or "") != str(user["user_id"])
                        or not accounts.owns_key(str(user["user_id"]), key_id)
                    ):
                        return
                page = await journal.events_after(request_id, key_id, cursor, limit=128)
                first_id = page.get("first_event_id")
                last_id = int(page.get("last_event_id") or 0)
                if first_id is not None and cursor < int(first_id) - 1:
                    snapshot = await journal.snapshot(request_id, key_id)
                    latest_row = telemetry.get(request_id) or {}
                    status_value = str(snapshot.get("terminal_status") or "")
                    if status_value == "completed":
                        payload = {
                            "status": "completed",
                            "terminal": True,
                            "response_text": str(
                                latest_row.get("response_text")
                                or snapshot.get("delivered_text") or ""
                            ),
                        }
                    elif status_value in {"stalled", "error", "cancelled"}:
                        payload = {
                            "status": status_value,
                            "terminal": True,
                            "partial_text": _best_partial_text(
                                latest_row.get("response_text_provisional"),
                                snapshot.get("delivered_text"),
                                snapshot.get("worker_partial_text"),
                            ),
                        }
                    else:
                        payload = {"status": "reset", "terminal": False}
                    reset_event_id = last_id if payload.get("terminal") is True else int(first_id) - 1
                    yield (
                        f"id: {reset_event_id}\nevent: replay.reset\ndata: "
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    )
                    if payload.get("terminal") is True:
                        return
                    cursor = int(first_id) - 1

                events = page.get("events") if isinstance(page.get("events"), list) else []
                latest_row = telemetry.get(request_id) or {}
                recovering_after_restart = str(latest_row.get("status") or "").lower() == "recovering"
                recovery_partial_sent = False
                for item in events:
                    sequence = int(item.get("id") or 0)
                    if sequence <= cursor:
                        continue
                    cursor = sequence
                    event_type = str(item.get("type") or "")
                    payload = item.get("data") if isinstance(item.get("data"), dict) else {}
                    if (
                        recovering_after_restart and not recovery_partial_sent
                        and event_type in {"worker.delta", "worker.snapshot", "worker.terminal"}
                    ):
                        # Worker-side events were intentionally not replayed as
                        # user-visible content during normal delivery. After a
                        # process restart, however, the browser has no live
                        # Broker stream. Send one bounded journal snapshot per
                        # page so the user sees provisional progress while the
                        # durable Worker terminal outbox is being reconciled.
                        snapshot = await journal.snapshot(request_id, key_id)
                        partial_text = _best_partial_text(
                            snapshot.get("delivered_text"),
                            snapshot.get("worker_partial_text"),
                        )
                        if partial_text:
                            yield (
                                f"id: {sequence}\nevent: recovery.partial.snapshot\ndata: "
                                + json.dumps({
                                    "partial_text": partial_text,
                                    "partial_text_unverified": True,
                                }, ensure_ascii=False, separators=(",", ":"))
                                + "\n\n"
                            )
                        recovery_partial_sent = True
                    if event_type in {"worker.delta", "worker.snapshot", "worker.terminal"}:
                        event_type = "cursor"
                        payload = {}
                    elif event_type == "delivery.stalled" and not payload.get("partial_text"):
                        snapshot = await journal.snapshot(request_id, key_id)
                        latest_row = telemetry.get(request_id) or {}
                        payload = {
                            **payload,
                            "partial_text": _best_partial_text(
                                latest_row.get("response_text_provisional"),
                                snapshot.get("delivered_text"),
                                snapshot.get("worker_partial_text"),
                            ),
                        }
                    yield (
                        f"id: {sequence}\nevent: {event_type}\ndata: "
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    )
                    if event_type in {
                        "delivery.completed", "delivery.error",
                        "delivery.cancelled", "delivery.stalled",
                    }:
                        return

                latest = telemetry.get(request_id) or {}
                latest_status = str(latest.get("status") or "unknown").lower()
                if latest_status in _TERMINAL:
                    snapshot = await journal.snapshot(request_id, key_id)
                    if latest_status == "completed":
                        payload = {
                            "status": "completed",
                            "terminal": True,
                            "response_text": str(
                                latest.get("response_text")
                                or snapshot.get("delivered_text") or ""
                            ),
                        }
                    else:
                        payload = {
                            "status": latest_status,
                            "terminal": True,
                            "partial_text": _best_partial_text(
                                latest.get("response_text_provisional"),
                                snapshot.get("delivered_text"),
                                snapshot.get("worker_partial_text"),
                            ),
                        }
                    yield (
                        f"id: {int(snapshot.get('last_event_id') or cursor)}"
                        "\nevent: recovery.terminal\ndata: "
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    )
                    return

                state = broker.requests.get(request_id)
                if latest_status == "running" and state is None:
                    snapshot = await journal.snapshot(request_id, key_id)
                    partial_text = _best_partial_text(
                        snapshot.get("delivered_text"),
                        snapshot.get("worker_partial_text"),
                    )
                    diagnostics = latest.get("diagnostics")
                    diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
                    diagnostics.update({
                        "web_chat_request_recovery": "broker-state-lost-during-replay",
                        "web_chat_event_replay_available": bool(snapshot.get("last_event_id")),
                        "web_chat_partial_text_unverified": bool(partial_text),
                    })
                    try:
                        await telemetry.upsert({
                            **latest,
                            "status": "stalled",
                            "response_text": partial_text,
                            "response_text_available": False,
                            "error": "The active Web Chat Broker lease is unavailable; no generation was redispatched",
                            "diagnostics": diagnostics,
                        })
                        await journal.append_terminal(
                            request_id, key_id, "stalled",
                            "The active Web Chat Broker lease is unavailable",
                        )
                    except Exception:
                        pass
                    snapshot = await journal.snapshot(request_id, key_id)
                    payload = {
                        "status": "stalled",
                        "terminal": True,
                        "partial_text": partial_text,
                    }
                    yield (
                        f"id: {int(snapshot.get('last_event_id') or cursor)}"
                        "\nevent: recovery.stalled\ndata: "
                        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                        + "\n\n"
                    )
                    return

                if await request.is_disconnected():
                    return
                now = time.monotonic()
                if now - last_heartbeat >= 10.0:
                    yield ": chat2api-replay-keepalive\n\n"
                    last_heartbeat = now
                await asyncio.sleep(0.25)

        return StreamingResponse(
            replay(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "private, no-store",
                "Vary": "Cookie",
                "X-Accel-Buffering": "no",
            },
        )


    @app.post("/api/user/chat/requests/{request_id}/cancel")
    async def cancel_chat_request(
        request_id: str, request: Request, response: Response,
    ) -> dict:
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Vary"] = "Cookie"
        if not _REQUEST_ID_RE.fullmatch(request_id):
            _http_error(404, "request not found")

        accounts, user = current_user(request)
        if not same_origin_admin_request(request):
            _http_error(403, "same-origin request required")

        telemetry = getattr(request.app.state, "telemetry", None)
        broker = getattr(request.app.state, "broker", None)
        registry = getattr(request.app.state, "registry", None)
        if telemetry is None or broker is None or registry is None:
            _http_error(503, "request cancellation unavailable")

        row = telemetry.get(request_id)
        if row is None:
            _http_error(404, "request not found")
        key_id = str(row.get("api_key_id") or "")
        if not key_id or not accounts.owns_key(str(user["user_id"]), key_id):
            _http_error(404, "request not found")

        lock = getattr(request.app.state, "web_chat_cancel_lock", None)
        if lock is None:
            _http_error(503, "request cancellation unavailable")
        queued_signal = None
        async with lock:
            row = telemetry.get(request_id)
            if row is None:
                _http_error(404, "request not found")
            key_id = str(row.get("api_key_id") or "")
            if not key_id or not accounts.owns_key(str(user["user_id"]), key_id):
                _http_error(404, "request not found")

            status = str(row.get("status") or "unknown").lower()
            if status in _TERMINAL:
                return {
                    "request_id": request_id,
                    "status": status,
                    "terminal": True,
                    "active": False,
                    "cancel_requested": False,
                    "already_terminal": True,
                }
            if status == "queued":
                signal = getattr(request.app.state, "web_chat_cancel_events", {}).get(request_id)
                if signal is None:
                    _http_error(409, "request is not awaiting cancellable Web Chat admission")
                already_requested = signal.requested.is_set()
                signal.requested.set()
                queued_signal = (signal, already_requested)
            elif status != "running":
                _http_error(409, "request is not currently dispatched to a Worker")
            else:
                state = broker.requests.get(request_id)
                if state is None:
                    _http_error(409, "active request state is unavailable")
                if getattr(state, "terminal_event_type", None):
                    return {
                        "request_id": request_id,
                        "status": "finalizing",
                        "terminal": False,
                        "active": True,
                        "cancel_requested": False,
                        "already_terminal": True,
                    }

                client_id = str(getattr(state, "client_id", "") or "")
                if not client_id:
                    _http_error(409, "request Worker assignment is unavailable")
                diagnostics = getattr(state, "diagnostics", None)
                if not isinstance(diagnostics, dict):
                    diagnostics = {}
                    state.diagnostics = diagnostics
                if diagnostics.get("web_chat_cancel_requested") is True:
                    return {
                        "request_id": request_id,
                        "status": "cancellation_requested",
                        "terminal": False,
                        "active": True,
                        "cancel_requested": True,
                        "already_requested": True,
                    }

                # This is only a signal to the assigned Worker. The Worker emits
                # the terminal event and RequestBroker remains the sole owner.
                diagnostics["web_chat_cancel_requested"] = True
                try:
                    await registry.send(
                        client_id, {"type": "chat.cancel", "request_id": request_id}
                    )
                except Exception:
                    diagnostics.pop("web_chat_cancel_requested", None)
                    _http_error(409, "request cancellation could not be delivered to its Worker")
                return {
                    "request_id": request_id,
                    "status": "cancellation_requested",
                    "terminal": False,
                    "active": True,
                    "cancel_requested": True,
                    "already_requested": False,
                }

        if queued_signal is not None:
            signal, already_requested = queued_signal
            try:
                await asyncio.wait_for(signal.finalized.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                pass
            latest = telemetry.get(request_id) or {}
            latest_status = str(latest.get("status") or "queued").lower()
            if latest_status in _TERMINAL:
                return {
                    "request_id": request_id,
                    "status": latest_status,
                    "terminal": True,
                    "active": False,
                    "cancel_requested": False,
                    "already_requested": already_requested,
                    "queued": True,
                }
            return {
                "request_id": request_id,
                "status": "cancellation_requested",
                "terminal": False,
                "active": True,
                "cancel_requested": True,
                "already_requested": already_requested,
                "queued": True,
            }


    return app
