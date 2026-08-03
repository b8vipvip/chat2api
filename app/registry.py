from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import WebSocket


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


class ClientRegistry:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "clients.json"
        self.clients: dict[str, PersistedClient] = {}
        self.sockets: dict[str, WebSocket] = {}
        self.socket_locks: dict[str, asyncio.Lock] = {}
        self.busy_clients: set[str] = set()
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            for raw in payload.get("clients", []):
                item = PersistedClient(**raw)
                self.clients[item.client_id] = item
        except (OSError, ValueError, TypeError):
            # A broken registry must not prevent the server from starting.
            self.clients = {}

    async def save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"clients": [vars(item) for item in self.clients.values()]}
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    async def register(self, name: str, browser_name: str, version: str, metadata: dict[str, Any]) -> tuple[str, str]:
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
                metadata=metadata,
            )
            await self.save()
            return client_id, token

    async def authenticate(self, client_id: str, token: str) -> bool:
        client = self.clients.get(client_id)
        return bool(client and secrets.compare_digest(client.token_hash, token_hash(token)))

    async def attach(self, client_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            old = self.sockets.get(client_id)
            self.sockets[client_id] = websocket
            self.socket_locks.setdefault(client_id, asyncio.Lock())
            client = self.clients[client_id]
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
        if metadata:
            client.metadata.update(metadata)

    async def send(self, client_id: str, payload: dict[str, Any]) -> None:
        websocket = self.sockets.get(client_id)
        if not websocket:
            raise RuntimeError("Chrome extension is offline")
        lock = self.socket_locks.setdefault(client_id, asyncio.Lock())
        async with lock:
            await websocket.send_json(payload)

    def online_client_ids(self) -> list[str]:
        return sorted(self.sockets)

    def resolve_client(self, requested: str | None) -> str:
        if requested:
            if requested not in self.clients:
                raise KeyError("Unknown client_id")
            if requested not in self.sockets:
                raise ConnectionError("Requested Chrome extension is offline")
            return requested
        online = self.online_client_ids()
        if not online:
            raise ConnectionError("No Chrome extension is online")
        if len(online) > 1:
            raise LookupError("Multiple extensions are online; provide client_id or X-Chat2API-Client")
        return online[0]

    def summaries(self) -> list[dict[str, Any]]:
        return [
            {
                "client_id": item.client_id,
                "name": item.name,
                "version": item.version,
                "online": item.client_id in self.sockets,
                "busy": item.client_id in self.busy_clients,
                "last_seen_at": item.last_seen_at,
                "metadata": item.metadata,
            }
            for item in self.clients.values()
        ]
