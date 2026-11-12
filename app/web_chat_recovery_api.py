"""W-04: owner-scoped snapshot recovery for disconnected Web Chat turns.

This endpoint observes the existing request-v6/Broker/Telemetry authorities.
It never redispatches a generation and never infers completion from transport EOF.
"""
from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Request, Response

from .user_commerce import USER_SESSION_COOKIE


_REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9_-]{8,120}$")
_TERMINAL = {"completed", "error", "cancelled", "stalled"}
_PRIVATE_HEADERS = {"Cache-Control": "private, no-store", "Vary": "Cookie"}


def _http_error(status_code: int, detail: str) -> None:
    raise HTTPException(status_code=status_code, detail=detail, headers=_PRIVATE_HEADERS)


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

        if status == "completed":
            final_text = str(row.get("response_text") or "")
            available = bool(row.get("response_text_available")) and bool(final_text)
            return {
                "request_id": request_id,
                "status": "completed",
                "terminal": True,
                "recoverable": False,
                "active": False,
                "response_text": final_text if available else "",
                "response_text_available": available,
                "updated_at": updated_at,
            }

        if status in _TERMINAL:
            # Failed/aborted/stalled candidate text is intentionally never
            # exposed as a completed assistant reply.
            return {
                "request_id": request_id,
                "status": status,
                "terminal": True,
                "recoverable": False,
                "active": False,
                "response_text": "",
                "response_text_available": False,
                "error_code": "request_" + status,
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
                "retry_after_ms": 1000,
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
                "retry_after_ms": 1000,
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
            "updated_at": updated_at,
        }

    return app
