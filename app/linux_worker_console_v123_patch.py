from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from .admin_auth import SESSION_COOKIE
from .linux_worker_installs import code_hash


PATCH_REVISION = 123
ASSET_PATH = "/assets/chat2api-linux-worker-console-v123.js"
ACTIVE_EXPIRES_AT = "9999-12-31T23:59:59Z"


class DeviceInstallCreate(BaseModel):
    pairing_id: str
    proxy_id: str


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


def _pairing_meta(worker: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(worker, dict):
        return {}
    metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
    value = metadata.get("worker_pairing") if isinstance(metadata.get("worker_pairing"), dict) else {}
    return dict(value)


def install_linux_worker_console_v123_patch(app: FastAPI) -> FastAPI:
    """Final Linux-device provisioning and presentation boundary.

    Device identity is the selected PairingStore name. A new physical device must
    select both a pairing code and a saved proxy before an install command can be
    issued. The proxy is applied automatically after the newly enrolled Agent is
    online; the pairing reference is attached before the Chrome extension binds.

    The browser asset renders a separate stable device table. Historical Linux
    table owners may keep their compatibility state in the hidden legacy table,
    but they no longer repaint the administrator-visible device rows.
    """
    if getattr(app.state, "linux_worker_console_v123_installed", False):
        return app

    required = (
        "linux_workers",
        "linux_worker_installs",
        "linux_worker_proxy_catalog",
        "pairings",
        "admin_sessions",
        "send_linux_worker_command",
    )
    if not all(hasattr(app.state, name) for name in required):
        return app

    app.state.linux_worker_console_v123_installed = True
    workers = app.state.linux_workers
    installs = app.state.linux_worker_installs
    catalog = app.state.linux_worker_proxy_catalog
    pairings = app.state.pairings
    sessions = app.state.admin_sessions
    proxy_tasks: dict[str, asyncio.Task[Any]] = {}
    last_proxy_attempt: dict[str, float] = {}

    def admin(request: Request) -> None:
        if not sessions.authenticate(request.cookies.get(SESSION_COOKIE)):
            raise HTTPException(status_code=401, detail="Administrator login required")

    def resolved_server(request: Request) -> str:
        return app.state.settings.resolved_public_url(str(request.base_url)).rstrip("/")

    def install_command(item: dict[str, Any], request: Request) -> str:
        server = resolved_server(request)
        return f"curl -fsSL {server}/bootstrap/linux-worker.sh | sudo bash -s -- --server {server} --enroll-code {item['code']}"

    def proxy_by_id(proxy_id: str) -> dict[str, Any] | None:
        return next((item for item in catalog.list() if str(item.get("proxy_id") or "") == str(proxy_id or "")), None)

    async def options_payload() -> dict[str, Any]:
        await pairings.ensure_loaded()
        pairing_rows = []
        for item in pairings.list_public():
            pairing_rows.append({
                "pairing_id": item.get("pairing_id"),
                "device_name": item.get("name"),
                "prefix": item.get("prefix"),
                "enabled": item.get("enabled") is True,
                "paired": bool(item.get("bound_client_id") or item.get("bound_device_id")),
                "bound_client_id": item.get("bound_client_id"),
            })
        proxies = [
            {
                "proxy_id": item.get("proxy_id"),
                "name": item.get("name"),
                "scheme": item.get("scheme"),
            }
            for item in catalog.list()
        ]
        return {"pairing_codes": pairing_rows, "proxies": proxies, "revision": PATCH_REVISION}

    def persist_setup_on_enrollment(item: dict[str, Any], pairing: Any, proxy: dict[str, Any]) -> None:
        install_id = str(item["install_id"])
        with installs._lock:
            stored = installs.data["installs"][install_id]
            stored.update({
                "name": str(pairing.name),
                "setup_pairing_id": str(pairing.pairing_id),
                "setup_pairing_name": str(pairing.name),
                "setup_pairing_prefix": str(pairing.prefix),
                "setup_proxy_id": str(proxy.get("proxy_id") or ""),
                "setup_proxy_name": str(proxy.get("name") or ""),
                "setup_revision": PATCH_REVISION,
            })
            installs._save()

        digest = code_hash(str(item["code"]).strip().upper())
        with workers._lock:
            enrollment = workers.data["enrollments"].get(digest)
            if enrollment is not None:
                enrollment.update({
                    "name": str(pairing.name),
                    "setup_pairing_id": str(pairing.pairing_id),
                    "setup_pairing_name": str(pairing.name),
                    "setup_pairing_prefix": str(pairing.prefix),
                    "setup_proxy_id": str(proxy.get("proxy_id") or ""),
                    "setup_proxy_name": str(proxy.get("name") or ""),
                    "setup_revision": PATCH_REVISION,
                })
                workers._save()

    @app.get("/api/admin/linux-device-setup-options")
    async def linux_device_setup_options(request: Request) -> dict[str, Any]:
        admin(request)
        return await options_payload()

    @app.post("/api/admin/linux-device-installations")
    async def create_linux_device_installation(body: DeviceInstallCreate, request: Request) -> dict[str, Any]:
        admin(request)
        await pairings.ensure_loaded()
        pairing = pairings.get(str(body.pairing_id or ""))
        if not pairing or not pairing.enabled:
            raise HTTPException(status_code=409, detail="请选择一个已启用的配对码")
        if pairing.bound_client_id or pairing.bound_device_id:
            raise HTTPException(status_code=409, detail="该配对码已经绑定设备，请为新设备选择未配对的配对码")
        proxy = proxy_by_id(str(body.proxy_id or ""))
        if not proxy:
            raise HTTPException(status_code=409, detail="请选择代理管理中已保存的代理")

        item = installs.create(str(pairing.name))
        digest = code_hash(str(item["code"]).strip().upper())
        with workers._lock:
            workers.data["enrollments"][digest] = {
                "code_hash": digest,
                "name": str(pairing.name),
                "created_at": item["created_at"],
                "expires_at": ACTIVE_EXPIRES_AT,
                "used_at": None,
                "install_id": item["install_id"],
            }
            workers._save()
        persist_setup_on_enrollment(item, pairing, proxy)
        item = installs.get(str(item["install_id"])) or item
        return {
            **item,
            "device_name": str(pairing.name),
            "pairing_id": str(pairing.pairing_id),
            "proxy_id": str(proxy.get("proxy_id") or ""),
            "proxy_name": str(proxy.get("name") or ""),
            "install_command": install_command(item, request),
            "revision": PATCH_REVISION,
        }

    # Attach the selected device identity before the Agent/extension starts
    # reporting. No raw pairing secret is copied to the Worker host.
    if not getattr(workers, "_chat2api_device_setup_v123_enroll", False):
        base_enroll = workers.enroll

        def enroll_with_device_setup(code: str, facts: dict[str, Any]) -> dict[str, str]:
            digest = code_hash(str(code or "").strip().upper())
            with workers._lock:
                enrollment_before = dict(workers.data["enrollments"].get(digest) or {})
            credentials = base_enroll(code, facts)
            worker_id = str(credentials.get("worker_id") or "")
            if not worker_id or not enrollment_before.get("setup_pairing_id"):
                return credentials
            with workers._lock:
                worker = workers.data["workers"].get(worker_id)
                if not worker:
                    return credentials
                metadata = dict(worker.get("metadata") or {})
                metadata["worker_pairing"] = {
                    "pairing_id": str(enrollment_before.get("setup_pairing_id") or ""),
                    "name": str(enrollment_before.get("setup_pairing_name") or worker.get("name") or ""),
                    "prefix": str(enrollment_before.get("setup_pairing_prefix") or ""),
                    "status": "pending",
                    "bound_client_id": None,
                    "bound_at": None,
                    "last_error": "",
                }
                metadata["device_setup_v123"] = {
                    "proxy_id": str(enrollment_before.get("setup_proxy_id") or ""),
                    "proxy_name": str(enrollment_before.get("setup_proxy_name") or ""),
                    "proxy_status": "pending",
                    "revision": PATCH_REVISION,
                }
                worker["metadata"] = metadata
                worker["worker_pairing_state"] = "pending"
                worker["name"] = str(enrollment_before.get("setup_pairing_name") or worker.get("name") or "Linux Worker")[:80]
                workers._save()
            return credentials

        workers.enroll = enroll_with_device_setup
        workers._chat2api_device_setup_v123_enroll = True

    def update_setup_state(worker_id: str, **values: Any) -> None:
        with workers._lock:
            worker = workers.data["workers"].get(worker_id)
            if not worker:
                return
            metadata = dict(worker.get("metadata") or {})
            setup = dict(metadata.get("device_setup_v123") or {}) if isinstance(metadata.get("device_setup_v123"), dict) else {}
            setup.update(values)
            metadata["device_setup_v123"] = setup
            worker["metadata"] = metadata
            workers._save()

    async def apply_pending_proxy(worker_id: str) -> None:
        try:
            worker = workers.data["workers"].get(worker_id)
            if not worker or worker.get("revoked_at"):
                return
            metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
            setup = metadata.get("device_setup_v123") if isinstance(metadata.get("device_setup_v123"), dict) else {}
            proxy_id = str(setup.get("proxy_id") or "")
            if not proxy_id or str(setup.get("proxy_status") or "") == "applied":
                return
            proxy = proxy_by_id(proxy_id)
            if not proxy:
                update_setup_state(worker_id, proxy_status="error", last_error="所选代理已从代理管理中删除")
                return
            update_setup_state(worker_id, proxy_status="applying", last_error="")
            command = await app.state.send_linux_worker_command(
                worker_id,
                "apply_proxy_config",
                {"share_link": str(proxy.get("share_link") or "")},
                wait=True,
                timeout=90,
            )
            result = command.get("result") if isinstance(command, dict) else {}
            if not isinstance(result, dict) or not result.get("ok"):
                raise RuntimeError(str((result or {}).get("error") or "proxy_apply_failed"))
            summary = result.get("proxy") if isinstance(result.get("proxy"), dict) else {}
            workers.record_proxy_success(worker_id, summary)
            with workers._lock:
                live = workers.data["workers"].get(worker_id)
                if live:
                    meta = dict(live.get("metadata") or {})
                    proxy_summary = dict(meta.get("proxy_summary") or {})
                    proxy_summary["name"] = str(proxy.get("name") or "代理")[:80]
                    meta["proxy_summary"] = proxy_summary
                    setup = dict(meta.get("device_setup_v123") or {})
                    setup.update({"proxy_status": "applied", "last_error": "", "applied_at": time.time()})
                    meta["device_setup_v123"] = setup
                    live["metadata"] = meta
                    workers._save()
        except Exception as exc:
            update_setup_state(worker_id, proxy_status="error", last_error=str(exc)[:160])
        finally:
            proxy_tasks.pop(worker_id, None)

    def schedule_pending_proxy(worker_id: str) -> None:
        worker = workers.data["workers"].get(worker_id)
        if not worker or worker.get("revoked_at"):
            return
        metadata = worker.get("metadata") if isinstance(worker.get("metadata"), dict) else {}
        setup = metadata.get("device_setup_v123") if isinstance(metadata.get("device_setup_v123"), dict) else {}
        if not setup.get("proxy_id") or str(setup.get("proxy_status") or "") == "applied":
            return
        if worker_id not in app.state.worker_sockets or worker_id in proxy_tasks:
            return
        now = time.monotonic()
        if now - last_proxy_attempt.get(worker_id, 0.0) < 20.0:
            return
        last_proxy_attempt[worker_id] = now
        try:
            proxy_tasks[worker_id] = asyncio.get_running_loop().create_task(apply_pending_proxy(worker_id))
        except RuntimeError:
            return

    if not getattr(workers, "_chat2api_device_setup_v123_heartbeat", False):
        base_heartbeat = workers.heartbeat

        def heartbeat_with_device_setup(worker_id: str, payload: dict[str, Any]) -> dict[str, Any]:
            result = base_heartbeat(worker_id, payload)
            schedule_pending_proxy(worker_id)
            return result

        workers.heartbeat = heartbeat_with_device_setup
        workers._chat2api_device_setup_v123_heartbeat = True

    async def decorate_device_rows(payload: dict[str, Any]) -> dict[str, Any]:
        await pairings.ensure_loaded()
        by_client = {
            str(item.bound_client_id): item.name
            for item in pairings.items.values()
            if item.bound_client_id and item.name
        }
        rows = payload.get("data") if isinstance(payload.get("data"), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            logical = row.get("device_workers") if isinstance(row.get("device_workers"), list) else []
            primary = logical[0] if logical else row
            meta = _pairing_meta(primary)
            client_id = str(primary.get("extension_client_id") or "") if isinstance(primary, dict) else ""
            name = str(meta.get("name") or by_client.get(client_id) or row.get("setup_pairing_name") or row.get("name") or "Linux 设备").strip()
            pairing_id = str(meta.get("pairing_id") or row.get("setup_pairing_id") or "")
            row["name"] = name
            row["device_name"] = name
            row["device_pairing_id"] = pairing_id or None
            for worker in logical:
                if not isinstance(worker, dict):
                    continue
                worker_meta = _pairing_meta(worker)
                worker_client = str(worker.get("extension_client_id") or "")
                worker["device_name"] = str(worker_meta.get("name") or by_client.get(worker_client) or name)
        payload["revision"] = PATCH_REVISION
        payload["stable_device_view"] = True
        return payload

    @app.get(ASSET_PATH, include_in_schema=False)
    async def linux_worker_console_v123_asset() -> Response:
        return Response(
            Path(__file__).with_name("admin_linux_worker_console_v123.js").read_text(encoding="utf-8"),
            media_type="application/javascript",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.middleware("http")
    async def linux_worker_console_v123(request: Request, call_next: Callable):
        response = await call_next(request)
        path = request.url.path
        if request.method == "GET" and path == "/api/admin/linux-worker-installations" and response.status_code == 200:
            raw = await _response_bytes(response)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return response
            if isinstance(payload, dict):
                payload = await decorate_device_rows(payload)
                headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
                headers["Cache-Control"] = "no-store"
                return JSONResponse(payload, status_code=response.status_code, headers=headers)

        if path == "/admin" and "text/html" in response.headers.get("content-type", ""):
            raw = await _response_bytes(response)
            text = raw.decode("utf-8", errors="replace")
            marker = f'<script src="{ASSET_PATH}"></script>'
            if marker not in text:
                text = text.replace("</body>", marker + "</body>")
            headers = {k: v for k, v in response.headers.items() if k.lower() not in {"content-length", "content-type"}}
            headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
            return Response(text, status_code=response.status_code, media_type="text/html", headers=headers)
        return response

    app.state.linux_worker_console_v123 = {"revision": PATCH_REVISION, "schedule_pending_proxy": schedule_pending_proxy}
    return app
