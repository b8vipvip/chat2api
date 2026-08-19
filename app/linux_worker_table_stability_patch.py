from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE


PATCH_VERSION = "0.22.19"
ASSET = "/assets/chat2api-linux-worker-stable-table-v22-19.js"


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


def _pairing_meta(worker: dict[str, Any]) -> dict[str, Any]:
    metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
    value = metadata.get("worker_pairing") if isinstance(metadata.get("worker_pairing"), dict) else {}
    return dict(value)


def install_linux_worker_table_stability_patch(app: FastAPI) -> FastAPI:
    """Own the final Linux Worker table presentation and destructive record cleanup.

    v0.22.18 layered a 10-column presentation over the legacy 12-column renderer.
    The legacy renderer refreshes once per second, so repeatedly removing two DOM
    cells caused the table width to jump. This patch leaves the source DOM at 12
    cells, hides the two retired columns with CSS, and paints the requested 10
    operational columns synchronously from cached backend data after each refresh.
    """

    if getattr(app.state, "linux_worker_table_stability_patch_installed", False):
        return app

    workers = app.state.linux_workers
    installs = getattr(app.state, "linux_worker_installs", None)
    pairings = app.state.pairings
    registry = app.state.registry
    login_sessions = app.state.worker_login_sessions
    app.state.linux_worker_table_stability_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    async def release_pairing(worker: dict[str, Any]) -> None:
        meta = _pairing_meta(worker)
        pairing_id = str(meta.get("pairing_id") or "")
        client_id = str(worker.get("extension_client_id") or "")
        device_id = str(worker.get("extension_device_id") or "")

        if pairing_id:
            await pairings.ensure_loaded()
            async with pairings.lock:
                item = pairings.items.get(pairing_id)
                if item and item.bound_client_id == client_id and (
                    not item.bound_device_id or not device_id or item.bound_device_id == device_id
                ):
                    item.bound_client_id = None
                    item.bound_device_id = None
                    await pairings.save()

        if client_id:
            async with registry.lock:
                client = registry.clients.get(client_id)
                if client and (not pairing_id or client.pairing_id == pairing_id):
                    client.pairing_id = None
                    metadata = dict(client.metadata or {})
                    metadata.pop("pairing_id", None)
                    metadata.pop("linux_worker_pairing_id", None)
                    client.metadata = metadata
                    await registry.save()

    @app.delete("/api/admin/linux-workers/{worker_id}/record")
    async def delete_worker_record(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        worker = workers.data["workers"].get(worker_id)
        if not worker:
            raise HTTPException(404, "Worker 不存在")

        await release_pairing(dict(worker))
        login_sessions.revoke(worker_id)

        socket = app.state.worker_sockets.pop(worker_id, None)
        if socket:
            try:
                await socket.close(code=4003, reason="Worker record deleted")
            except Exception:
                pass

        linked_install_ids: set[str] = set()
        with workers._lock:
            for enrollment in workers.data.get("enrollments", {}).values():
                if str(enrollment.get("worker_id") or "") == worker_id:
                    install_id = str(enrollment.get("install_id") or "")
                    if install_id:
                        linked_install_ids.add(install_id)
            workers.data["enrollments"] = {
                key: value
                for key, value in workers.data.get("enrollments", {}).items()
                if str(value.get("worker_id") or "") != worker_id
            }
            workers.data["workers"].pop(worker_id, None)
            workers._save()

        removed_installs = 0
        if installs is not None:
            with installs._lock:
                for install_id, item in list(installs.data.get("installs", {}).items()):
                    if install_id in linked_install_ids or str(item.get("worker_id") or "") == worker_id:
                        installs.data["installs"].pop(install_id, None)
                        removed_installs += 1
                if removed_installs:
                    installs._save()

        return {"deleted": True, "worker_id": worker_id, "removed_install_records": removed_installs}

    @app.get(ASSET, include_in_schema=False)
    async def linux_worker_stable_table_asset() -> Response:
        path = Path(__file__).with_name("admin_linux_worker_stable_table.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.middleware("http")
    async def linux_worker_stable_table_ui(request: Request, call_next):
        response = await call_next(request)
        if request.url.path != "/admin" or "text/html" not in response.headers.get("content-type", ""):
            return response

        raw = await _response_bytes(response)
        text = raw.decode("utf-8", errors="replace")
        blocker = (
            "<script>"
            "globalThis.__CHAT2API_LINUX_WORKER_PAIRING_UI_V22_18__=true;"
            "globalThis.__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__=true;"
            "</script>"
        )
        if blocker not in text:
            text = text.replace("</head>", blocker + "</head>")
        marker = f'<script src="{ASSET}"></script>'
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
