from __future__ import annotations

from typing import Any, Awaitable, Callable

from starlette.responses import JSONResponse


ASGIApp = Callable[[dict[str, Any], Callable[..., Any], Callable[..., Any]], Awaitable[None]]

MODE_HEADER = b"x-chat2api-mode"
API_PREFIX = "/v1"
MODE2_VALUES = frozenset({"api", "mode2"})
MODE1_VALUES = frozenset({"chat", "mode1"})


class ModeResolverMiddleware:
    """Resolve the API mode once at ingress without changing OpenAI API payloads.

    Standard /v1 requests always resolve to Mode 2 when the optional extension
    header is absent. The header is accepted only on /v1 routes. Mode 1 is not
    an API transport: attempts to select it on /v1 are rejected rather than
    forwarded to any web proxy. No proxy route is created by this middleware.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    def _is_api_path(path: str) -> bool:
        return path == API_PREFIX or path.startswith(API_PREFIX + "/")

    @staticmethod
    def _set_mode_state(scope: dict[str, Any], *, source: str) -> None:
        state = scope.get("state")
        if not isinstance(state, dict):
            state = {}
            scope["state"] = state
        state["chat2api_mode"] = "mode2"
        state["chat2api_mode_source"] = source
        state["chat2api_entrypoint"] = "openai_api"

    @staticmethod
    async def _reject(scope: dict[str, Any], receive, send, *, message: str, code: str) -> None:
        response = JSONResponse(
            status_code=400,
            content={
                "error": {
                    "message": message,
                    "type": "invalid_request_error",
                    "param": "X-Chat2API-Mode",
                    "code": code,
                }
            },
        )
        await response(scope, receive, send)

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        # Do not inspect WebSocket handshakes or any non-HTTP ASGI scope.
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        mode_headers = [
            value for name, value in scope.get("headers", ())
            if name.lower() == MODE_HEADER
        ]
        if len(mode_headers) > 1:
            await self._reject(
                scope,
                receive,
                send,
                message="Only one X-Chat2API-Mode header may be supplied.",
                code="duplicate_mode_header",
            )
            return

        path = str(scope.get("path") or "")
        if not self._is_api_path(path):
            if mode_headers:
                await self._reject(
                    scope,
                    receive,
                    send,
                    message="X-Chat2API-Mode is supported only on /v1 API requests.",
                    code="mode_header_not_supported_for_entrypoint",
                )
                return
            await self.app(scope, receive, send)
            return

        if not mode_headers:
            self._set_mode_state(scope, source="default")
            await self.app(scope, receive, send)
            return

        try:
            requested_mode = mode_headers[0].decode("ascii").strip().lower()
        except UnicodeDecodeError:
            requested_mode = ""

        if requested_mode in MODE2_VALUES:
            self._set_mode_state(scope, source="header")
            await self.app(scope, receive, send)
            return

        if requested_mode in MODE1_VALUES:
            await self._reject(
                scope,
                receive,
                send,
                message=(
                    "Mode 1 is not an OpenAI API transport. Use of Mode 1 on /v1 "
                    "is an entrypoint conflict; /v1 requests remain Mode 2."
                ),
                code="mode_entrypoint_conflict",
            )
            return

        await self._reject(
            scope,
            receive,
            send,
            message="Unsupported X-Chat2API-Mode value. Supported API values are 'api' and 'mode2'.",
            code="invalid_mode",
        )
