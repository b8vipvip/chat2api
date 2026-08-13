from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import WebSocket

from .timezone_utils import beijing_now_iso, to_beijing_iso


def utc_now() -> str:
    """Backward-compatible helper name; canonical timestamps are Asia/Shanghai."""
    return beijing_now_iso()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _model_id(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("id") or "").strip()
    return ""


@dataclass
class PersistedClient:
    client_id: str
    name: str
    browser_name: str
    version: str
    token_hash: str
    created_at: str
    last_seen_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    device_id: str | None = None
    pairing_id: str | None = None
    connection_enabled: bool = True


class ClientRegistry:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "clients.json"
        self.clients: dict[str, PersistedClient] = {}
        self.sockets: dict[str, WebSocket] = {}
        self.socket_locks: dict[str, asyncio.Lock] = {}
        self.busy_clients: set[str] = set()
        self.api_key_routes: dict[str, str] = {}
        self.routing_key_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
            "chat2api_api_key_route", default=None
        )
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in payload.get("clients", []):
                if not isinstance(raw, dict):
                    continue
                value = dict(raw)
                if value.get("created_at"):
                    value["created_at"] = to_beijing_iso(value["created_at"]) or value["created_at"]
                if value.get("last_seen_at"):
                    value["last_seen_at"] = to_beijing_iso(value["last_seen_at"]) or value["last_seen_at"]
                item = PersistedClient(**value)
                self.clients[item.client_id] = item
            routes = payload.get("api_key_routes")
            if isinstance(routes, dict):
                self.api_key_routes = {
                    str(key): str(client_id)
                    for key, client_id in routes.items()
                    if str(key) and str(client_id)
                }
        except (OSError, ValueError, TypeError):
            self.clients = {}
            self.api_key_routes = {}

    async def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "clients": [vars(item) for item in self.clients.values()],
            "api_key_routes": dict(self.api_key_routes),
        }
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    def set_routing_key(self, key_id: str | None) -> None:
        self.routing_key_context.set(str(key_id) if key_id else None)

    def route_for_key(self, key_id: str) -> str | None:
        return self.api_key_routes.get(str(key_id))

    def _remember_route(self, key_id: str | None, client_id: str) -> None:
        if not key_id:
            return
        if self.api_key_routes.get(key_id) == client_id:
            return
        self.api_key_routes[key_id] = client_id
        try:
            asyncio.get_running_loop().create_task(self.save())
        except RuntimeError:
            pass

    async def register(
        self,
        name: str,
        browser_name: str,
        version: str,
        metadata: dict[str, Any],
        *,
        device_id: str | None = None,
        pairing_id: str | None = None,
    ) -> tuple[str, str]:
        async with self.lock:
            client_id = "ext_" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
            token = secrets.token_urlsafe(32)
            self.clients[client_id] = PersistedClient(
                client_id=client_id,
                name=name,
                browser_name=browser_name,
                version=version,
                token_hash=token_hash(token),
                created_at=utc_now(),
                metadata=dict(metadata),
                device_id=str(device_id or "").strip() or None,
                pairing_id=str(pairing_id or "").strip() or None,
            )
            await self.save()
            return client_id, token

    async def rotate_token(
        self,
        client_id: str,
        *,
        name: str | None = None,
        browser_name: str | None = None,
        version: str | None = None,
        metadata: dict[str, Any] | None = None,
        device_id: str | None = None,
        pairing_id: str | None = None,
    ) -> tuple[str, str]:
        async with self.lock:
            item = self.clients.get(client_id)
            if not item:
                raise KeyError("Unknown client_id")
            token = secrets.token_urlsafe(32)
            item.token_hash = token_hash(token)
            if name:
                item.name = str(name)[:120]
            if browser_name:
                item.browser_name = str(browser_name)[:80]
            if version:
                item.version = str(version)[:40]
            if metadata:
                item.metadata.update(dict(metadata))
            if device_id:
                item.device_id = str(device_id)
            if pairing_id:
                item.pairing_id = str(pairing_id)
            item.connection_enabled = True
            await self.save()
            return client_id, token

    async def authenticate(self, client_id: str, token: str) -> bool:
        client = self.clients.get(client_id)
        return bool(
            client
            and client.connection_enabled
            and secrets.compare_digest(client.token_hash, token_hash(token))
        )

    async def attach(self, client_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            client = self.clients[client_id]
            if not client.connection_enabled:
                raise PermissionError("Extension connection is disabled by administrator")
            old = self.sockets.get(client_id)
            self.sockets[client_id] = websocket
            self.socket_locks.setdefault(client_id, asyncio.Lock())
            client.last_seen_at = utc_now()
            await self.save()
        if old and old is not websocket:
            try:
                await old.close(code=4001, reason="Replaced by a newer connection")
            except RuntimeError:
                pass

    async def detach(self, client_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            if self.sockets.get(client_id) is websocket:
                self.sockets.pop(client_id, None)

    async def touch(self, client_id: str, metadata: dict[str, Any] | None = None) -> None:
        client = self.clients.get(client_id)
        if not client:
            return
        client.last_seen_at = utc_now()
        changed = False
        if metadata:
            for key, value in metadata.items():
                if client.metadata.get(key) != value:
                    client.metadata[key] = value
                    changed = True
            reported_device = str(metadata.get("device_id") or "").strip()
            if reported_device and client.device_id != reported_device:
                client.device_id = reported_device
                changed = True
        if changed:
            await self.save()

    async def set_connection_enabled(self, client_id: str, enabled: bool) -> PersistedClient:
        async with self.lock:
            item = self.clients.get(client_id)
            if not item:
                raise KeyError("Unknown client_id")
            item.connection_enabled = bool(enabled)
            socket = self.sockets.get(client_id)
            await self.save()
        if not enabled and socket:
            try:
                await socket.close(code=4003, reason="Disconnected by administrator")
            except RuntimeError:
                pass
        return item

    async def delete_client(self, client_id: str) -> PersistedClient:
        """Delete one persisted extension identity and any sticky routes to it."""
        async with self.lock:
            item = self.clients.pop(client_id, None)
            if not item:
                raise KeyError("Unknown client_id")
            socket = self.sockets.pop(client_id, None)
            self.socket_locks.pop(client_id, None)
            self.busy_clients.discard(client_id)
            self.api_key_routes = {
                key_id: routed_client
                for key_id, routed_client in self.api_key_routes.items()
                if routed_client != client_id
            }
            await self.save()
        if socket:
            try:
                await socket.close(code=4004, reason="Extension history deleted by administrator")
            except RuntimeError:
                pass
        return item

    async def send(self, client_id: str, payload: dict[str, Any]) -> None:
        client = self.clients.get(client_id)
        if not client or not client.connection_enabled:
            raise RuntimeError("Chrome extension connection is disabled")
        websocket = self.sockets.get(client_id)
        if not websocket:
            raise RuntimeError("Chrome extension is offline")
        lock = self.socket_locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            await websocket.send_json(payload)

    def online_client_ids(self) -> list[str]:
        return sorted(
            client_id for client_id in self.sockets
            if self.clients.get(client_id) and self.clients[client_id].connection_enabled
        )

    def resolve_client(self, requested: str | None) -> str:
        key_id = self.routing_key_context.get()
        if requested:
            if requested not in self.clients:
                raise KeyError("Unknown client_id")
            client = self.clients[requested]
            if not client.connection_enabled:
                raise ConnectionError("Requested Chrome extension is disabled by administrator")
            if requested not in self.sockets:
                raise ConnectionError("Requested Chrome extension is offline")
            self._remember_route(key_id, requested)
            return requested

        online = self.online_client_ids()
        if not online:
            raise ConnectionError("No Chrome extension is online. Open Chrome with a paired chat2api extension.")

        if key_id:
            previous = self.api_key_routes.get(key_id)
            if previous in online:
                return previous

        selected = secrets.choice(online)
        self._remember_route(key_id, selected)
        return selected

    def client_models(self, client_id: str) -> list[dict[str, Any]]:
        client = self.clients.get(client_id)
        if not client:
            return []
        models = client.metadata.get("models")
        if not isinstance(models, list):
            return []
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in models:
            if isinstance(raw, str):
                item = {"id": raw, "label": raw, "capabilities": ["text"]}
            elif isinstance(raw, dict):
                item = dict(raw)
            else:
                continue
            model_id = _model_id(item)
            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            item["id"] = model_id
            item.setdefault("label", model_id)
            item.setdefault("capabilities", ["text"])
            result.append(item)
        return result

    def supports_model(self, client_id: str, model_id: str) -> bool:
        _ = client_id
        return bool(str(model_id or "").strip())

    def model_catalog(self, online_only: bool = True) -> list[dict[str, Any]]:
        catalog: dict[str, dict[str, Any]] = {
            "default": {
                "id": "default", "object": "model", "created": 0, "owned_by": "chat2api",
                "label": "ChatGPT current selection (zero-touch)",
                "capabilities": ["text", "vision", "file-understanding"], "clients": [],
            },
            "chatgpt-web": {
                "id": "chatgpt-web", "object": "model", "created": 0, "owned_by": "chat2api",
                "label": "Compatibility alias for default",
                "capabilities": ["text", "vision", "file-understanding"], "clients": [],
            },
            "gpt-image": {
                "id": "gpt-image", "object": "model", "created": 0, "owned_by": "chat2api",
                "label": "ChatGPT Images browser route",
                "capabilities": ["image-generation", "image-reference"], "clients": [],
            },
            "gpt-live": {
                "id": "gpt-live", "object": "model", "created": 0, "owned_by": "chat2api",
                "label": "ChatGPT Voice / GPT-Live route",
                "capabilities": ["voice-generation", "voice-conversation", "text"], "clients": [],
            },
            "gpt-live-mini": {
                "id": "gpt-live-mini", "object": "model", "created": 0, "owned_by": "chat2api",
                "label": "ChatGPT Voice / GPT-Live mini route",
                "capabilities": ["voice-generation", "voice-conversation", "text"], "clients": [],
            },
        }
        client_ids = self.online_client_ids() if online_only else [
            client_id for client_id, item in self.clients.items() if item.connection_enabled
        ]
        base_ids = ("default", "chatgpt-web", "gpt-image", "gpt-live", "gpt-live-mini")
        for client_id in client_ids:
            for base_id in base_ids:
                if client_id not in catalog[base_id]["clients"]:
                    catalog[base_id]["clients"].append(client_id)
            for model in self.client_models(client_id):
                model_id = str(model["id"])
                entry = catalog.setdefault(
                    model_id,
                    {
                        "id": model_id,
                        "object": "model",
                        "created": 0,
                        "owned_by": "chat2api",
                        "label": model.get("label") or model_id,
                        "capabilities": model.get("capabilities") or ["text"],
                        "family": model.get("family"),
                        "reasoning": model.get("reasoning"),
                        "clients": [],
                    },
                )
                if client_id not in entry["clients"]:
                    entry["clients"].append(client_id)
                if model.get("selected"):
                    entry["selected_on"] = client_id
        order = {"default": 0, "chatgpt-web": 1, "gpt-image": 2, "gpt-live": 3, "gpt-live-mini": 4}
        return sorted(catalog.values(), key=lambda item: (order.get(str(item["id"]), 5), str(item["id"])))

    def summaries(self) -> list[dict[str, Any]]:
        bound_counts: dict[str, int] = {}
        for client_id in self.api_key_routes.values():
            bound_counts[client_id] = bound_counts.get(client_id, 0) + 1
        return [
            {
                "client_id": item.client_id,
                "name": item.name,
                "browser_name": item.browser_name,
                "version": item.version,
                "online": item.client_id in self.sockets and item.connection_enabled,
                "busy": item.client_id in self.busy_clients,
                "connection_enabled": item.connection_enabled,
                "device_id": item.device_id,
                "pairing_id": item.pairing_id,
                "bound_api_keys": bound_counts.get(item.client_id, 0),
                "last_seen_at": to_beijing_iso(item.last_seen_at) if item.last_seen_at else None,
                "created_at": to_beijing_iso(item.created_at) if item.created_at else None,
                "metadata": item.metadata,
            }
            for item in self.clients.values()
        ]
