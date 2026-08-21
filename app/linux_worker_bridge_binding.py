from __future__ import annotations

import hashlib
import secrets
import threading
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Request


PATCH_VERSION = "0.22.3"
BINDING_TICKET_TTL_SECONDS = 180
MAX_BINDING_BODY_TEXT = 240


@dataclass
class BindingTicket:
    worker_id: str
    digest: str
    expires_at: float


class WorkerBridgeBindingTicketStore:
    """Memory-only, worker-bound, one-time extension binding tickets."""

    def __init__(self, *, now: Callable[[], float] = time.monotonic, ttl_seconds: int = BINDING_TICKET_TTL_SECONDS) -> None:
        self._now = now
        self._ttl_seconds = max(30, int(ttl_seconds))
        self._lock = threading.RLock()
        self._tickets: dict[str, BindingTicket] = {}
        self._worker_ticket: dict[str, str] = {}

    @staticmethod
    def _digest(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _purge(self) -> None:
        now = self._now()
        for digest, item in list(self._tickets.items()):
            if item.expires_at > now:
                continue
            self._tickets.pop(digest, None)
            if self._worker_ticket.get(item.worker_id) == digest:
                self._worker_ticket.pop(item.worker_id, None)

    def issue(self, worker_id: str) -> str:
        worker_id = str(worker_id)
        raw = "wbind_" + secrets.token_urlsafe(32)
        digest = self._digest(raw)
        with self._lock:
            self._purge()
            previous = self._worker_ticket.pop(worker_id, None)
            if previous:
                self._tickets.pop(previous, None)
            self._tickets[digest] = BindingTicket(worker_id=worker_id, digest=digest, expires_at=self._now() + self._ttl_seconds)
            self._worker_ticket[worker_id] = digest
        return raw

    def require(self, raw: str) -> BindingTicket:
        digest = self._digest(str(raw or ""))
        with self._lock:
            self._purge()
            item = self._tickets.get(digest)
            if not item:
                raise KeyError("Invalid or expired worker binding ticket")
            return item

    def consume(self, raw: str) -> BindingTicket:
        digest = self._digest(str(raw or ""))
        with self._lock:
            self._purge()
            item = self._tickets.pop(digest, None)
            if not item:
                raise KeyError("Invalid or expired worker binding ticket")
            if self._worker_ticket.get(item.worker_id) == digest:
                self._worker_ticket.pop(item.worker_id, None)
            return item


def _safe_text(value: Any, limit: int = MAX_BINDING_BODY_TEXT) -> str:
    return str(value or "").strip()[:limit]


def install_linux_worker_bridge_binding_patch(app: FastAPI) -> FastAPI:
    if getattr(app.state, "linux_worker_bridge_binding_installed", False):
        return app

    workers = app.state.linux_workers
    registry = app.state.registry
    tickets = WorkerBridgeBindingTicketStore()
    app.state.worker_bridge_binding_tickets = tickets
    app.state.linux_worker_bridge_binding_installed = True

    async def sync_client(client_id: str) -> None:
        worker = workers.worker_for_extension(client_id)
        if not worker:
            return
        item = registry.clients.get(client_id)
        if not item:
            workers.clear_extension_binding(worker["worker_id"], expected_client_id=client_id)
            return
        metadata = dict(item.metadata or {})
        try:
            workers.record_extension_status(
                worker["worker_id"],
                {
                    "client_id": client_id,
                    "device_id": item.device_id or metadata.get("device_id") or "",
                    "version": metadata.get("extension_version") or item.version,
                    "online": client_id in registry.sockets and item.connection_enabled,
                    "connection_enabled": item.connection_enabled,
                    "metadata": metadata,
                },
            )
        except (KeyError, TypeError, ValueError):
            return

    async def ensure_binding_metadata(client_id: str, worker_id: str, device_id: str) -> None:
        await registry.touch(
            client_id,
            {
                "linux_worker_id": worker_id,
                "linux_worker_binding_version": 30,
                "linux_worker_binding_source": "worker-ticket-v30",
                "device_id": device_id,
                "extension_id": client_id,
                "worker_id": worker_id,
                "platform": "linux",
                "status": "connected",
                "last_seen": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def clear_registry_binding_metadata(client_id: str) -> None:
        if client_id not in registry.clients:
            return
        await registry.touch(
            client_id,
            {
                "linux_worker_id": "",
                "linux_worker_binding_version": 0,
                "linux_worker_binding_source": "replaced-offline-worker-binding-v30",
            },
        )

    def client_online(client_id: str) -> bool:
        item = registry.clients.get(client_id)
        return bool(item and item.connection_enabled and client_id in registry.sockets)

    # Keep Worker state synchronized with the authoritative extension registry.
    if not getattr(registry, "_chat2api_linux_worker_binding_v30", False):
        base_touch = registry.touch
        base_attach = registry.attach
        base_detach = registry.detach
        base_set_connection_enabled = registry.set_connection_enabled
        base_delete_client = registry.delete_client

        async def touch_with_worker_sync(client_id: str, metadata: dict[str, Any] | None = None) -> None:
            await base_touch(client_id, metadata)
            await sync_client(client_id)

        async def attach_with_worker_sync(client_id: str, websocket) -> None:
            await base_attach(client_id, websocket)
            await sync_client(client_id)

        async def detach_with_worker_sync(client_id: str, websocket) -> None:
            await base_detach(client_id, websocket)
            await sync_client(client_id)

        async def set_connection_enabled_with_worker_sync(client_id: str, enabled: bool):
            item = await base_set_connection_enabled(client_id, enabled)
            await sync_client(client_id)
            return item

        async def delete_client_with_worker_cleanup(client_id: str):
            item = await base_delete_client(client_id)
            workers.clear_extension_binding_by_client(client_id)
            return item

        registry.touch = touch_with_worker_sync
        registry.attach = attach_with_worker_sync
        registry.detach = detach_with_worker_sync
        registry.set_connection_enabled = set_connection_enabled_with_worker_sync
        registry.delete_client = delete_client_with_worker_cleanup
        registry._chat2api_linux_worker_binding_v30 = True

    @app.post("/api/workers/extension-binding-ticket")
    async def issue_extension_binding_ticket(request: Request) -> dict[str, Any]:
        worker_id = _safe_text(request.headers.get("x-worker-id"), 120)
        worker_token = str(request.headers.get("x-worker-token") or "")
        if not worker_id or not worker_token or not workers.authenticate(worker_id, worker_token):
            raise HTTPException(401, "Invalid Worker credentials")
        worker = workers.data["workers"].get(worker_id)
        if not worker or worker.get("revoked_at"):
            raise HTTPException(409, "Worker is unavailable")

        current_client_id = _safe_text(worker.get("extension_client_id"), 160)
        if current_client_id:
            if current_client_id in registry.clients:
                await ensure_binding_metadata(
                    current_client_id,
                    worker_id,
                    _safe_text(worker.get("extension_device_id") or registry.clients[current_client_id].device_id, 200),
                )
                await sync_client(current_client_id)
                if client_online(current_client_id):
                    return {"bound": True, "online": True, "worker_id": worker_id, "client_id": current_client_id, "version": PATCH_VERSION}
            else:
                workers.clear_extension_binding(worker_id, expected_client_id=current_client_id)
                current_client_id = ""

        # A durable binding with an offline client intentionally receives a fresh
        # proof. The same profile can reuse its existing credentials; if storage
        # was lost, the Worker may replace only this offline identity.
        ticket = tickets.issue(worker_id)
        server_url = app.state.settings.resolved_public_url(str(request.base_url)).rstrip("/")
        return {
            "bound": False,
            "worker_id": worker_id,
            "current_client_id": current_client_id or None,
            "ticket": ticket,
            "expires_in_seconds": BINDING_TICKET_TTL_SECONDS,
            "retry_after_seconds": 60,
            "server_url": server_url,
            "version": PATCH_VERSION,
        }

    @app.post("/api/extensions/worker-bind")
    async def claim_worker_extension_binding(request: Request) -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "Invalid worker binding JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "Invalid worker binding JSON")

        raw_ticket = str(body.get("ticket") or "")
        device_id = _safe_text(body.get("device_id"), 200)
        if len(device_id) < 8:
            raise HTTPException(400, "Extension device_id is required")
        client_id = _safe_text(body.get("client_id"), 180)
        client_token = str(body.get("client_token") or "")
        if bool(client_id) != bool(client_token):
            raise HTTPException(400, "client_id and client_token must be supplied together")

        # Consume before any async identity mutation so two concurrent claims can
        # never both pass the same proof.
        try:
            binding = tickets.consume(raw_ticket)
        except KeyError as exc:
            raise HTTPException(401, "Invalid or expired worker binding ticket") from exc

        worker = workers.data["workers"].get(binding.worker_id)
        if not worker or worker.get("revoked_at"):
            raise HTTPException(409, "Worker is unavailable")

        metadata = dict(body.get("metadata") or {}) if isinstance(body.get("metadata"), dict) else {}
        safe_metadata = {
            "runtime_id": _safe_text(metadata.get("runtime_id"), 160),
            "device_id": device_id,
            "extension_version": _safe_text(body.get("version") or metadata.get("extension_version"), 40),
            "linux_worker_id": binding.worker_id,
            "linux_worker_binding_version": 30,
            "linux_worker_binding_source": "worker-ticket-v30",
        }
        safe_metadata = {key: value for key, value in safe_metadata.items() if value not in {"", None}}

        if client_id:
            if not await registry.authenticate(client_id, client_token):
                raise HTTPException(401, "Invalid extension credentials")
            item = registry.clients.get(client_id)
            if item and item.device_id and str(item.device_id) != device_id:
                raise HTTPException(409, "Extension credentials belong to another device_id")
            previous_worker = _safe_text((item.metadata or {}).get("linux_worker_id"), 120) if item else ""
            if previous_worker and previous_worker != binding.worker_id:
                previous = workers.data["workers"].get(previous_worker)
                if previous and not previous.get("revoked_at"):
                    raise HTTPException(409, "Extension is already bound to another active Linux Worker")

        current_client_id = _safe_text(worker.get("extension_client_id"), 180)
        if current_client_id and current_client_id != client_id:
            if client_online(current_client_id):
                raise HTTPException(409, "Linux Worker already has an online bound extension")
            workers.clear_extension_binding(binding.worker_id, expected_client_id=current_client_id)
            await clear_registry_binding_metadata(current_client_id)

        created = False
        token: str | None = None
        if not client_id:
            client_id, token = await registry.register(
                _safe_text(body.get("name"), 120) or f"Linux Worker {worker.get('name') or binding.worker_id}",
                _safe_text(body.get("browser_name"), 80) or "Chrome",
                _safe_text(body.get("version"), 40) or "unknown",
                safe_metadata,
                device_id=device_id,
                pairing_id=None,
            )
            created = True

        try:
            workers.bind_extension(binding.worker_id, client_id, device_id)
        except ValueError as exc:
            if created:
                await registry.delete_client(client_id)
            raise HTTPException(409, str(exc)) from exc

        await ensure_binding_metadata(client_id, binding.worker_id, device_id)
        await sync_client(client_id)

        result: dict[str, Any] = {
            "bound": True,
            "worker_id": binding.worker_id,
            "client_id": client_id,
            "reused": not created,
            "version": PATCH_VERSION,
        }
        if token:
            result["token"] = token
        return result

    return app
