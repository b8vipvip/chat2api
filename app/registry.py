from __future__ import annotations

import asyncio
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
                if not isinstance(raw, dict):
                    continue
                value = dict(raw)
                if value.get("created_at"):
                    value["created_at"] = to_beijing_iso(value["created_at"]) or value["created_at"]
                if value.get("last_seen_at"):
                    value["last_seen_at"] = to_beijing_iso(value["last_seen_at"]) or value["last_seen_at"]
                item = PersistedClient(**value)
                self.clients[item.client_id] = item
        except (OSError, ValueError, TypeError):
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
                metadata=dict(metadata),
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
        changed = False
        if metadata:
            for key, value in metadata.items():
                if client.metadata.get(key) != value:
                    client.metadata[key] = value
                    changed = True
        if changed:
            await self.save()

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
            raise ConnectionError("No Chrome extension is online. Open Chrome with the paired chat2api extension.")
        if len(online) > 1:
            raise LookupError("Multiple extensions are online; provide client_id or X-Chat2API-Client")
        return online[0]

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
        client_ids = self.online_client_ids() if online_only else list(self.clients)
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
        return [
            {
                "client_id": item.client_id,
                "name": item.name,
                "version": item.version,
                "online": item.client_id in self.sockets,
                "busy": item.client_id in self.busy_clients,
                "last_seen_at": to_beijing_iso(item.last_seen_at) if item.last_seen_at else None,
                "metadata": item.metadata,
            }
            for item in self.clients.values()
        ]
