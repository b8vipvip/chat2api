from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .admin_auth import SESSION_COOKIE
from .worker_limits_clipboard_v121_patch import install_worker_limits_clipboard_v121_patch
from .linux_worker_device_console_v122_patch import install_linux_worker_device_console_v122_patch
from .linux_worker_console_v123_patch import install_linux_worker_console_v123_patch


PATCH_REVISION = 66
ADMIN_ASSET = "/assets/chat2api-worker-presentation-v66.js"


class PairingNameUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


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


def _install_v121_if_ready(app: FastAPI) -> None:
    required = (
        "registry",
        "admin_sessions",
        "linux_workers",
        "worker_login_sessions",
        "send_linux_worker_command",
    )
    if all(hasattr(app.state, name) for name in required):
        install_worker_limits_clipboard_v121_patch(app)


def _install_v122_if_ready(app: FastAPI) -> None:
    required = (
        "linux_workers",
        "linux_worker_installs",
        "admin_sessions",
    )
    if all(hasattr(app.state, name) for name in required):
        install_linux_worker_device_console_v122_patch(app)


def _install_v123_if_ready(app: FastAPI) -> None:
    required = (
        "linux_workers",
        "linux_worker_installs",
        "linux_worker_proxy_catalog",
        "pairings",
        "admin_sessions",
        "send_linux_worker_command",
    )
    if all(hasattr(app.state, name) for name in required):
        install_linux_worker_console_v123_patch(app)


def install_worker_presentation_v64_patch(app: FastAPI) -> FastAPI:
    """Expose Worker presentation data with a bounded passive console enhancer.

    v64/v65 maintained autonomous MutationObservers and refresh timers against the
    same Worker table already owned by admin_extension_columns. v66 keeps device
    name/occupancy/rename behavior, but the enhancer has no MutationObserver and
    no repeating interval: it only runs after canonical reload/show boundaries
    plus two bounded startup passes. This prevents a second autonomous render loop
    from starving the admin console while preserving the presentation features.
    """
    if getattr(app.state, "worker_presentation_v66_installed", False):
        _install_v121_if_ready(app)
        _install_v122_if_ready(app)
        _install_v123_if_ready(app)
        return app
    app.state.worker_presentation_v66_installed = True

    registry = app.state.registry
    pairings = app.state.pairings
    sessions = app.state.admin_sessions

    if not getattr(registry, "_chat2api_worker_device_name_v64", False):
        base_summaries = registry.summaries

        def summaries_with_device_names() -> list[dict[str, Any]]:
            rows = base_summaries()
            by_pairing: dict[str, str] = {}
            by_client: dict[str, tuple[str, str]] = {}
            for pairing in pairings.items.values():
                name = str(pairing.name or "").strip()
                pairing_id = str(pairing.pairing_id or "").strip()
                if pairing_id and name:
                    by_pairing[pairing_id] = name
                client_id = str(pairing.bound_client_id or "").strip()
                if client_id and name:
                    by_client[client_id] = (pairing_id, name)

            decorated: list[dict[str, Any]] = []
            for raw in rows:
                row = dict(raw)
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                pairing_id = str(row.get("pairing_id") or metadata.get("pairing_id") or "").strip()
                client_id = str(row.get("client_id") or "").strip()
                fallback_pairing, fallback_name = by_client.get(client_id, ("", ""))
                if not pairing_id:
                    pairing_id = fallback_pairing
                device_name = by_pairing.get(pairing_id) or fallback_name
                row["device_code_id"] = pairing_id or None
                row["device_name"] = device_name or None
                decorated.append(row)
            return decorated

        registry.summaries = summaries_with_device_names
        registry._chat2api_worker_device_name_v64 = True

    @app.patch("/api/admin/pairing-codes/{pairing_id}/name")
    async def rename_pairing_code(pairing_id: str, body: PairingNameUpdate, request: Request) -> dict[str, Any]:
        if not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Administrator login required")
        clean = str(body.name or "").strip()
        if not clean:
            raise HTTPException(status_code=422, detail="设备名称不能为空")
        await pairings.ensure_loaded()
        async with pairings.lock:
            item = pairings.items.get(pairing_id)
            if not item:
                raise HTTPException(status_code=404, detail="Unknown pairing code")
            item.name = clean[:120]
            await pairings.save()
            payload = item.public()

        # The pairing name is the single administrator-facing device-name source.
        # Mirror the new value into Linux compatibility caches so every page changes
        # atomically instead of waiting for the next extension reconciliation.
        workers = getattr(app.state, "linux_workers", None)
        if workers is not None:
            with workers._lock:
                changed = False
                for worker in workers.data.get("workers", {}).values():
                    metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
                    pairing_meta = metadata.get("worker_pairing") if isinstance(metadata.get("worker_pairing"), dict) else {}
                    if str(pairing_meta.get("pairing_id") or "") != pairing_id:
                        continue
                    metadata = dict(metadata)
                    pairing_meta = dict(pairing_meta)
                    pairing_meta["name"] = payload.get("name")
                    metadata["worker_pairing"] = pairing_meta
                    worker["metadata"] = metadata
                    worker["name"] = str(payload.get("name") or worker.get("name") or "Linux Worker")[:80]
                    changed = True
                if changed:
                    workers._save()

        installs = getattr(app.state, "linux_worker_installs", None)
        if installs is not None:
            with installs._lock:
                changed = False
                for install in installs.data.get("installs", {}).values():
                    if str(install.get("setup_pairing_id") or "") != pairing_id:
                        continue
                    install["setup_pairing_name"] = payload.get("name")
                    install["name"] = payload.get("name")
                    changed = True
                if changed:
                    installs._save()
        return {"pairing": payload, "device_name": payload.get("name"), "revision": PATCH_REVISION}

    @app.get(ADMIN_ASSET, include_in_schema=False)
    async def worker_presentation_asset() -> Response:
        path = Path(__file__).with_name("admin_worker_presentation_v66.js")
        return Response(
            path.read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def worker_presentation_v66(request: Request, call_next):
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

    _install_v121_if_ready(app)
    _install_v122_if_ready(app)
    # v123 supersedes only the Linux physical-device presentation/provisioning
    # surface. Worker routing, capacity and same-host slot authorities stay intact.
    _install_v123_if_ready(app)
    return app
