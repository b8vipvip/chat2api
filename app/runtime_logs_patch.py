from __future__ import annotations

import logging
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .runtime_logs import RuntimeLogStore, install_runtime_log_handler
from .timezone_utils import beijing_now


PATCH_VERSION = "0.22.23"
ASSET_PATH = "/assets/chat2api-runtime-logs.js"
logger = logging.getLogger("chat2api.runtime_logs")


async def _response_bytes(response: Response) -> bytes:
    body = getattr(response, "body", None)
    if body is not None:
        return bytes(body)
    chunks: list[bytes] = []
    iterator = getattr(response, "body_iterator", None)
    if iterator is not None:
        async for chunk in iterator:
            chunks.append(chunk.encode() if isinstance(chunk, str) else bytes(chunk))
    return b"".join(chunks)


def install_runtime_logs_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "runtime_logs_patch_installed", False):
        return app

    store = RuntimeLogStore(app.state.settings.data_dir)
    install_runtime_log_handler(store)
    app.state.runtime_logs = store
    app.state.runtime_logs_patch_installed = True

    def require_admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if sessions and sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            return
        authorization = str(request.headers.get("authorization") or "")
        supplied = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
        master = str(getattr(app.state.settings, "api_key", "") or "")
        if supplied and master and secrets.compare_digest(supplied, master):
            return
        raise HTTPException(401, "Administrator session required")

    @app.get(ASSET_PATH, include_in_schema=False)
    async def runtime_logs_asset() -> Response:
        path = Path(__file__).with_name("admin_runtime_logs.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get("/api/admin/runtime-logs")
    async def runtime_logs(
        request: Request,
        limit: int = Query(default=500, ge=1, le=5000),
        level: str | None = Query(default=None),
        logger_name: str | None = Query(default=None, alias="logger"),
        q: str | None = Query(default=None),
    ) -> dict:
        require_admin(request)
        return {
            **store.query(limit=limit, level=level, logger_name=logger_name, q=q),
            "version": PATCH_VERSION,
        }

    @app.get("/api/admin/runtime-logs/export")
    async def runtime_logs_export(
        request: Request,
        limit: int = Query(default=5000, ge=1, le=5000),
        level: str | None = Query(default=None),
        logger_name: str | None = Query(default=None, alias="logger"),
        q: str | None = Query(default=None),
    ) -> Response:
        require_admin(request)
        payload = store.export_text(limit=limit, level=level, logger_name=logger_name, q=q)
        stamp = beijing_now().strftime("%Y%m%d-%H%M%S")
        logger.info(
            "Administrator exported runtime logs limit=%s level=%s logger=%s query=%s",
            limit,
            level or "*",
            logger_name or "*",
            q or "*",
        )
        return Response(
            payload,
            media_type="text/plain; charset=utf-8",
            headers={
                "Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="chat2api-runtime-logs-{stamp}.log"',
            },
        )

    @app.middleware("http")
    async def runtime_logs_console_and_exceptions(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled HTTP exception method=%s path=%s",
                request.method,
                request.url.path,
                extra={"method": request.method, "path": request.url.path},
            )
            raise

        path = request.url.path
        if response.status_code >= 500:
            logger.error(
                "HTTP %s method=%s path=%s",
                response.status_code,
                request.method,
                path,
                extra={"method": request.method, "path": path, "status_code": response.status_code},
            )

        content_type = response.headers.get("content-type", "")
        if path == "/admin" and "text/html" in content_type:
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ASSET_PATH}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {
                key: value
                for key, value in response.headers.items()
                if key.lower() not in {"content-length", "content-type"}
            }
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        return response

    logger.info("Runtime log capture installed with persistent redacted export")
    return app
