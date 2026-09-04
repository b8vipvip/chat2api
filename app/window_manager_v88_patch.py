from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE


PATCH_REVISION = 88
ADMIN_ASSET = "/assets/chat2api-window-manager-v88.js"


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


def install_window_manager_v88_patch(app: FastAPI) -> FastAPI:
    """Expose the authoritative Worker browser-window registry to administrators.

    The browser owns physical window lifecycle and screenshot capture.  The server
    only aggregates the most recent Worker-reported snapshot and forwards an
    authenticated capture command over the already-authorized Worker socket.
    """
    if getattr(app.state, "window_manager_v88_installed", False):
        return app
    app.state.window_manager_v88_installed = True

    registry = app.state.registry
    sessions = app.state.admin_sessions

    def require_admin(request: Request) -> None:
        if not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Administrator login required")

    def window_rows() -> dict[str, Any]:
        active: list[dict[str, Any]] = []
        closed: list[dict[str, Any]] = []
        workers: list[dict[str, Any]] = []
        for summary in registry.summaries():
            row = dict(summary)
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            snapshot = metadata.get("window_manager_v88") if isinstance(metadata.get("window_manager_v88"), dict) else {}
            client_id = str(row.get("client_id") or "")
            device_name = str(row.get("device_name") or metadata.get("device_name") or row.get("name") or "").strip() or None
            device_code_id = str(row.get("device_code_id") or row.get("pairing_id") or metadata.get("pairing_id") or "").strip() or None
            workers.append({
                "client_id": client_id,
                "device_name": device_name,
                "device_code_id": device_code_id,
                "online": bool(row.get("online")),
                "window_manager_revision": metadata.get("window_manager_revision"),
                "window_selection_policy": metadata.get("window_selection_policy"),
            })
            for raw in snapshot.get("active") or []:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item.update({
                    "client_id": client_id,
                    "device_name": device_name,
                    "device_code_id": device_code_id,
                    "worker_online": bool(row.get("online")),
                })
                active.append(item)
            for raw in snapshot.get("closed") or []:
                if not isinstance(raw, dict):
                    continue
                item = dict(raw)
                item.update({
                    "client_id": client_id,
                    "device_name": device_name,
                    "device_code_id": device_code_id,
                    "worker_online": bool(row.get("online")),
                })
                closed.append(item)
        active.sort(key=lambda item: (int(item.get("window_no") or 10**9), int(item.get("opened_at_ms") or 10**18)))
        closed.sort(key=lambda item: int(item.get("closed_at_ms") or 0), reverse=True)
        return {
            "revision": PATCH_REVISION,
            "policy": "oldest-ready-fifo-v88",
            "active": active,
            "closed": closed,
            "workers": workers,
        }

    @app.get("/api/admin/window-manager")
    async def admin_window_manager(request: Request) -> dict[str, Any]:
        require_admin(request)
        return window_rows()

    @app.post("/api/admin/window-manager/{client_id}/{window_id}/capture")
    async def admin_capture_window(client_id: str, window_id: int, request: Request) -> dict[str, Any]:
        require_admin(request)
        if client_id not in registry.clients:
            raise HTTPException(status_code=404, detail="Unknown Worker")
        if client_id not in registry.sockets:
            raise HTTPException(status_code=409, detail="Worker is offline")
        control_id = "window_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
        try:
            await registry.send(client_id, {
                "type": "window.manager.capture",
                "control_id": control_id,
                "window_id": int(window_id),
            })
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {
            "accepted": True,
            "control_id": control_id,
            "client_id": client_id,
            "window_id": int(window_id),
            "revision": PATCH_REVISION,
        }

    @app.get(ADMIN_ASSET, include_in_schema=False)
    async def window_manager_asset() -> Response:
        path = Path(__file__).with_name("admin_window_manager_v88.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def inject_window_manager(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response
        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        marker = f'<script src="{ADMIN_ASSET}"></script>'
        if marker not in text:
            text = text.replace("</body>", marker + "</body>")
        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() not in {"content-length", "content-type"}
        }
        headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)

    return app
