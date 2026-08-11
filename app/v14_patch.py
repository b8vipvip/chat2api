from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from . import diagnostics as diagnostics_module
from .timezone_utils import BEIJING_TZ, beijing_now_iso, to_beijing_iso


PATCH_VERSION = "0.14.1"
TIMEZONE_NAME = "Asia/Shanghai"
UTC_OFFSET = "+08:00"
_TIME_KEYS = {
    "at", "created_at", "updated_at", "recorded_at", "started_at", "finished_at",
    "ended_at", "saved_at", "last_seen_at", "last_used_at", "expires_at", "revoked_at",
    "paired_at", "socket_updated_at", "last_activity_at",
}


class BeijingFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        value = datetime.fromtimestamp(record.created, tz=BEIJING_TZ)
        if datefmt:
            return value.strftime(datefmt)
        return value.isoformat(timespec="milliseconds")


async def _response_bytes(response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def _normalize_json_times(value: Any, key: str | None = None) -> Any:
    if isinstance(value, dict):
        return {name: _normalize_json_times(item, name) for name, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json_times(item, key) for item in value]
    if isinstance(value, str) and key in _TIME_KEYS:
        return to_beijing_iso(value) or value
    return value


def _install_beijing_diagnostics() -> None:
    diagnostics_module.utc_stamp = beijing_now_iso
    logger = logging.getLogger("chat2api")
    for handler in logger.handlers:
        handler.setFormatter(BeijingFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))


def install_v14_patch(app: FastAPI) -> FastAPI:
    app.version = PATCH_VERSION
    _install_beijing_diagnostics()

    @app.get("/assets/chat2api-v14.js")
    async def admin_v14_js() -> Response:
        path = Path(__file__).with_name("admin_v14.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def v14_beijing_time_and_console(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        content_type = response.headers.get("content-type", "")

        if "application/json" in content_type:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return Response(raw, status_code=response.status_code, media_type="application/json")
            payload = _normalize_json_times(payload)
            if isinstance(payload, dict) and path in {"/", "/healthz", "/api/admin/overview"}:
                payload["version"] = PATCH_VERSION
                payload["timezone"] = TIMEZONE_NAME
                payload["utc_offset"] = UTC_OFFSET
                if path == "/api/admin/overview":
                    capabilities = payload.setdefault("capabilities", {})
                    if isinstance(capabilities, dict):
                        capabilities["beijing_time_standard"] = True
                        capabilities["timestamp_timezone"] = TIMEZONE_NAME
                        capabilities["reasoning_family_recovery"] = True
                        capabilities["adaptive_reasoning_slider"] = True
                        capabilities["beijing_console_no_double_conversion"] = True
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            if path.startswith("/api/admin/") or path in {"/", "/healthz"}:
                headers["Cache-Control"] = "no-store"
            return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path in {"/admin", "/developers"} and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = '<script src="/assets/chat2api-v14.js"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

        return response

    return app
