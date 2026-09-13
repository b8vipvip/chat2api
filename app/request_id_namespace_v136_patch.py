from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse


PATCH_REVISION = 136
LOGGER = logging.getLogger("chat2api.request_id_namespace")
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
PATH_PREFIXES = {
    "/v1/chat/completions": ("req_", "chat"),
    "/v1/completions": ("req_", "completions"),
    "/v1/images/generations": ("imgreq_", "images"),
    "/v1/responses": ("resp_req_", "responses"),
}


def _native_request_id(value: str, prefix: str) -> bool:
    return bool(re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_-]{8,120}", value))


def _header_value(headers: list[tuple[bytes, bytes]], name: bytes) -> str:
    lowered = name.lower()
    for key, value in headers:
        if key.lower() == lowered:
            return value.decode("latin-1").strip()
    return ""


def _replace_header(headers: list[tuple[bytes, bytes]], name: bytes, value: str) -> list[tuple[bytes, bytes]]:
    lowered = name.lower()
    result = [(key, item) for key, item in headers if key.lower() != lowered]
    result.append((name, value.encode("ascii")))
    return result


class RequestIdNamespaceMiddlewareV136:
    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        contract = PATH_PREFIXES.get(path)
        if not contract:
            await self.app(scope, receive, send)
            return

        prefix, protocol = contract
        trace_id = "api_" + uuid.uuid4().hex
        headers = list(scope.get("headers") or [])
        upstream_id = _header_value(headers, b"x-chat2api-request-id")
        normalized = False

        if not upstream_id:
            internal_id = prefix + uuid.uuid4().hex
        elif _native_request_id(upstream_id, prefix):
            internal_id = upstream_id
        elif upstream_id.startswith(prefix) or not SAFE_REQUEST_ID.fullmatch(upstream_id):
            LOGGER.warning(
                "API ingress rejected invalid request id trace_id=%s path=%s protocol=%s expected_prefix=%s supplied=%s",
                trace_id,
                path,
                protocol,
                prefix,
                upstream_id[:160],
            )
            response = JSONResponse(
                {"detail": f"Invalid {prefix} request ID"},
                status_code=400,
                headers={"X-Chat2API-Trace-ID": trace_id},
            )
            await response(scope, receive, send)
            return
        else:
            # A syntactically safe ID from a different protocol is correlation
            # metadata, not this endpoint's broker identity. Generate a native ID
            # instead of failing a Responses->Chat (or Chat->Responses) fallback.
            internal_id = prefix + uuid.uuid4().hex
            normalized = True

        headers = _replace_header(headers, b"x-chat2api-request-id", internal_id)
        headers = _replace_header(headers, b"x-chat2api-trace-id", trace_id)
        next_scope = dict(scope)
        next_scope["headers"] = headers

        LOGGER.info(
            "API ingress trace_id=%s path=%s protocol=%s internal_request_id=%s upstream_request_id=%s normalized=%s",
            trace_id,
            path,
            protocol,
            internal_id,
            upstream_id or "-",
            normalized,
        )

        async def send_with_trace(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                response_headers = list(message.get("headers") or [])
                response_headers.append((b"x-chat2api-trace-id", trace_id.encode("ascii")))
                response_headers.append((b"x-chat2api-internal-request-id", internal_id.encode("ascii")))
                if normalized and upstream_id:
                    response_headers.append((b"x-chat2api-upstream-request-id", upstream_id.encode("ascii")))
                LOGGER.info(
                    "API response trace_id=%s path=%s protocol=%s internal_request_id=%s status_code=%s",
                    trace_id,
                    path,
                    protocol,
                    internal_id,
                    int(message.get("status") or 0),
                )
                message = dict(message)
                message["headers"] = response_headers
            await send(message)

        await self.app(next_scope, receive, send_with_trace)


def install_request_id_namespace_v136_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "request_id_namespace_v136_installed", False):
        return app
    app.add_middleware(RequestIdNamespaceMiddlewareV136)
    app.state.request_id_namespace_v136_installed = True
    app.state.request_id_namespace_v136 = {
        "revision": PATCH_REVISION,
        "paths": sorted(PATH_PREFIXES),
        "cross_protocol_ids_normalized": True,
        "trace_header": "X-Chat2API-Trace-ID",
    }
    return app
