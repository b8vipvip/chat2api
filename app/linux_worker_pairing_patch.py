from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

from .admin_auth import SESSION_COOKIE
from .timezone_utils import beijing_now_iso


PATCH_VERSION = "0.22.18"
PAIRING_ASSET = "/assets/chat2api-linux-worker-pairing-v22-18.js"
MAX_PAIRING_CODE_LENGTH = 512
MAX_PROXY_NAME_LENGTH = 80
PAIRING_STATES = {"pending", "waiting_chatgpt_login", "detecting_extension", "binding", "bound", "failed"}
logger = logging.getLogger(__name__)


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


def install_linux_worker_pairing_patch(app: FastAPI) -> FastAPI:
    """Add admin-selected pairing codes to the existing Worker↔Bridge bootstrap.

    The raw pairing code is never copied into linux_workers.json. The admin paste
    is matched against PairingStore's encrypted/hash-backed record and only the
    safe pairing id/name/prefix are attached to the Worker. The existing Worker
    ticket still bootstraps the extension identity; once authoritative Bridge
    telemetry says ChatGPT is logged in, the same extension client is associated
    with the selected pairing record without rotating its extension token.
    """

    if getattr(app.state, "linux_worker_pairing_patch_installed", False):
        return app

    workers = app.state.linux_workers
    registry = app.state.registry
    pairings = app.state.pairings
    app.state.linux_worker_pairing_patch_installed = True

    def admin(request: Request) -> None:
        sessions = getattr(app.state, "admin_sessions", None)
        if not sessions or not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(401, "Administrator session required")

    def worker_exists(worker_id: str) -> dict[str, Any]:
        worker = workers.data["workers"].get(worker_id)
        if not worker:
            raise HTTPException(404, "Worker not found")
        if worker.get("revoked_at"):
            raise HTTPException(409, "Worker is revoked")
        return worker

    async def pairing_from_raw(raw_code: str):
        await pairings.ensure_loaded()
        digest = hashlib.sha256(raw_code.encode("utf-8")).hexdigest()
        async with pairings.lock:
            for item in pairings.items.values():
                if not item.enabled or not item.code_hash:
                    continue
                if secrets.compare_digest(item.code_hash, digest):
                    return item
        return None

    def write_pairing_state(worker_id: str, values: dict[str, Any] | None) -> dict[str, Any]:
        with workers._lock:
            worker = workers.data["workers"].get(worker_id)
            if not worker:
                raise KeyError(worker_id)
            metadata = dict(worker.get("metadata") or {})
            if values is None:
                metadata.pop("worker_pairing", None)
                worker["worker_pairing_state"] = "pending"
            else:
                current = dict(metadata.get("worker_pairing") or {}) if isinstance(metadata.get("worker_pairing"), dict) else {}
                current.update(values)
                current["updated_at"] = beijing_now_iso()
                metadata["worker_pairing"] = current
                state = str(current.get("status") or "pending")
                worker["worker_pairing_state"] = state if state in PAIRING_STATES else "failed"
            worker["metadata"] = metadata
            workers._save()
            return workers.public(worker)

    async def unbind_previous(worker: dict[str, Any], pairing_id: str) -> None:
        if not pairing_id:
            return
        client_id = str(worker.get("extension_client_id") or "")
        device_id = str(worker.get("extension_device_id") or "")
        await pairings.ensure_loaded()
        async with pairings.lock:
            item = pairings.items.get(pairing_id)
            if item and item.bound_client_id == client_id and (not item.bound_device_id or item.bound_device_id == device_id):
                item.bound_client_id = None
                item.bound_device_id = None
                await pairings.save()
        if client_id:
            async with registry.lock:
                client = registry.clients.get(client_id)
                if client and client.pairing_id == pairing_id:
                    client.pairing_id = None
                    metadata = dict(client.metadata or {})
                    metadata.pop("pairing_id", None)
                    metadata.pop("linux_worker_pairing_id", None)
                    client.metadata = metadata
                    await registry.save()

    async def reconcile_pairing(worker_id: str) -> dict[str, Any]:
        worker = workers.data["workers"].get(worker_id)
        if not worker or worker.get("revoked_at"):
            return {"configured": False, "status": "unavailable"}
        configured = _pairing_meta(worker)
        pairing_id = str(configured.get("pairing_id") or "")
        if not pairing_id:
            return {"configured": False, "status": "not_configured"}

        if str(worker.get("chatgpt_status") or "").lower() != "ready":
            write_pairing_state(worker_id, {"status": "waiting_chatgpt_login", "last_error": ""})
            return {"configured": True, "status": "waiting_chatgpt_login", "pairing_id": pairing_id}

        logger.info("[linux-worker] ChatGPT login detected worker_id=%s", worker_id)

        client_id = str(worker.get("extension_client_id") or "")
        device_id = str(worker.get("extension_device_id") or "")
        if not client_id or len(device_id) < 8:
            write_pairing_state(worker_id, {"status": "detecting_extension", "last_error": ""})
            return {"configured": True, "status": "detecting_extension", "pairing_id": pairing_id}

        logger.info("[linux-worker] extension detected worker_id=%s extension_id=%s", worker_id, client_id)

        await pairings.ensure_loaded()
        pairing = pairings.get(pairing_id)
        if not pairing or not pairing.enabled:
            write_pairing_state(worker_id, {"status": "failed", "last_error": "配对码不存在或已停用"})
            return {"configured": True, "status": "failed", "error": "pairing_unavailable"}
        if pairing.bound_device_id and pairing.bound_device_id != device_id:
            write_pairing_state(worker_id, {"status": "failed", "last_error": "配对码已绑定到其他扩展设备"})
            return {"configured": True, "status": "failed", "error": "pairing_device_mismatch"}

        client = registry.clients.get(client_id)
        if not client:
            write_pairing_state(worker_id, {"status": "detecting_extension", "last_error": "扩展身份尚未同步到中心"})
            return {"configured": True, "status": "detecting_extension", "pairing_id": pairing_id}
        if client.device_id and str(client.device_id) != device_id:
            write_pairing_state(worker_id, {"status": "failed", "last_error": "Worker 与扩展设备标识不一致"})
            return {"configured": True, "status": "failed", "error": "extension_device_mismatch"}

        write_pairing_state(worker_id, {"status": "binding", "last_error": ""})
        logger.info("[linux-worker] pairing matched worker_id=%s pairing_id=%s", worker_id, pairing_id)

        try:
            await pairings.bind(pairing_id, client_id, device_id)
        except (KeyError, PermissionError) as exc:
            write_pairing_state(worker_id, {"status": "failed", "last_error": str(exc)[:160]})
            return {"configured": True, "status": "failed", "error": "pairing_bind_failed"}

        async with registry.lock:
            client = registry.clients.get(client_id)
            if not client:
                write_pairing_state(worker_id, {"status": "detecting_extension", "last_error": "扩展身份已离线或删除"})
                return {"configured": True, "status": "detecting_extension", "pairing_id": pairing_id}
            client.pairing_id = pairing_id
            metadata = dict(client.metadata or {})
            metadata["pairing_id"] = pairing_id
            metadata["linux_worker_pairing_id"] = pairing_id
            metadata.update({"extension_id": client_id, "worker_id": worker_id, "platform": "linux", "status": "connected", "last_seen": beijing_now_iso()})
            client.metadata = metadata
            await registry.save()

        bound_at = beijing_now_iso()
        write_pairing_state(
            worker_id,
            {
                "pairing_id": pairing_id,
                "name": pairing.name,
                "prefix": pairing.prefix,
                "status": "bound",
                "bound_client_id": client_id,
                "extension_id": client_id,
                "worker_id": worker_id,
                "bound_at": bound_at,
                "last_sync_at": bound_at,
                "last_error": "",
            },
        )
        logger.info("[linux-worker] extension enrollment completed worker_id=%s extension_id=%s", worker_id, client_id)
        return {"configured": True, "status": "bound", "pairing_id": pairing_id, "client_id": client_id}

    # The Bridge binding patch calls this method whenever authoritative extension
    # telemetry changes. Wrapping it here makes a saved pairing code bind as soon
    # as the extension reports ChatGPT ready, while the Agent's regular binding
    # ticket poll below remains a fallback reconciliation path.
    if not getattr(workers, "_chat2api_pairing_v22_18_wrapped", False):
        base_record_extension_status = workers.record_extension_status

        def record_extension_status_with_pairing(worker_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
            result = base_record_extension_status(worker_id, snapshot)
            if str(result.get("chatgpt_status") or "").lower() == "ready" and _pairing_meta(result).get("pairing_id"):
                try:
                    asyncio.get_running_loop().create_task(reconcile_pairing(worker_id))
                except RuntimeError:
                    pass
            return result

        workers.record_extension_status = record_extension_status_with_pairing
        workers._chat2api_pairing_v22_18_wrapped = True

    @app.put("/api/admin/linux-workers/{worker_id}/pairing-code")
    async def save_worker_pairing_code(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        worker = worker_exists(worker_id)
        body = await request.json()
        raw_code = str(body.get("pairing_code") or "").strip()
        if not raw_code or len(raw_code) > MAX_PAIRING_CODE_LENGTH or "\n" in raw_code or "\r" in raw_code:
            raise HTTPException(400, "配对码为空、过长或不是单行文本")
        pairing = await pairing_from_raw(raw_code)
        if not pairing:
            raise HTTPException(401, "配对码无效或已停用")

        device_id = str(worker.get("extension_device_id") or "")
        if pairing.bound_device_id and device_id and pairing.bound_device_id != device_id:
            raise HTTPException(409, "该配对码已绑定到其他扩展设备")

        previous = _pairing_meta(worker)
        previous_id = str(previous.get("pairing_id") or "")
        if previous_id and previous_id != pairing.pairing_id:
            await unbind_previous(worker, previous_id)

        write_pairing_state(
            worker_id,
            {
                "pairing_id": pairing.pairing_id,
                "name": pairing.name,
                "prefix": pairing.prefix,
                "status": "pending",
                "bound_client_id": None,
                "bound_at": None,
                "last_error": "",
            },
        )
        result = await reconcile_pairing(worker_id)
        current = _pairing_meta(workers.data["workers"][worker_id])
        return {"saved": True, "pairing": current, "reconcile": result}

    @app.delete("/api/admin/linux-workers/{worker_id}/pairing-code")
    async def clear_worker_pairing_code(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        worker = worker_exists(worker_id)
        current = _pairing_meta(worker)
        pairing_id = str(current.get("pairing_id") or "")
        if pairing_id:
            await unbind_previous(worker, pairing_id)
        write_pairing_state(worker_id, None)
        return {"cleared": True}

    @app.post("/api/admin/linux-workers/{worker_id}/proxy-label")
    async def save_worker_proxy_label(worker_id: str, request: Request) -> dict[str, Any]:
        admin(request)
        worker = worker_exists(worker_id)
        if str(worker.get("proxy_status") or "").lower() not in {"connected", "ready"}:
            raise HTTPException(409, "Worker 代理尚未连接")
        body = await request.json()
        name = str(body.get("name") or "").strip()[:MAX_PROXY_NAME_LENGTH]
        if not name:
            raise HTTPException(400, "代理名称不能为空")
        with workers._lock:
            live = workers.data["workers"][worker_id]
            metadata = dict(live.get("metadata") or {})
            summary = dict(metadata.get("proxy_summary") or {}) if isinstance(metadata.get("proxy_summary"), dict) else {}
            if not summary:
                raise HTTPException(409, "Worker 尚未回传代理摘要")
            summary["name"] = name
            metadata["proxy_summary"] = summary
            live["metadata"] = metadata
            workers._save()
        return {"saved": True, "name": name}

    @app.get(PAIRING_ASSET, include_in_schema=False)
    async def linux_worker_pairing_asset() -> Response:
        path = Path(__file__).with_name("admin_linux_worker_pairing.js")
        return Response(path.read_text(encoding="utf-8"), media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @app.middleware("http")
    async def linux_worker_pairing_ui_and_agent_reconcile(request: Request, call_next):
        response = await call_next(request)
        path = request.url.path

        if path == "/api/workers/extension-binding-ticket" and request.method == "POST":
            worker_id = str(request.headers.get("x-worker-id") or "")
            worker_token = str(request.headers.get("x-worker-token") or "")
            if worker_id and worker_token and workers.authenticate(worker_id, worker_token):
                try:
                    asyncio.get_running_loop().create_task(reconcile_pairing(worker_id))
                except RuntimeError:
                    pass

        if path == "/admin" and "text/html" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{PAIRING_ASSET}"></script>'
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

    return app
