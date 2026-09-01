from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.linux_worker_enable_patch as enable_patch
import app.worker_disable_authority_patch as authority_patch
from app.admin_auth import SESSION_COOKIE
from app.linux_worker_enable_patch import install_linux_worker_enable_patch
from app.worker_disable_authority_patch import install_worker_disable_authority_patch


class Sessions:
    def authenticate(self, token: str | None) -> bool:
        return token == "admin-ok"


class Workers:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.saved = 0
        self.data = {
            "workers": {
                "wrk_linux": {
                    "worker_id": "wrk_linux",
                    "name": "Linux Worker",
                    "enabled": True,
                    "revoked_at": None,
                    "status": "ready",
                    "chatgpt_status": "ready",
                    "extension_client_id": "ext_linux",
                    "metadata": {"bridge": {"extension_id": "ext_linux"}},
                }
            }
        }

    def _save(self) -> None:
        self.saved += 1

    def public(self, worker):
        return dict(worker)


class Registry:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.clients = {
            "ext_linux": SimpleNamespace(client_id="ext_linux", connection_enabled=True, metadata={}),
        }
        self.sockets: dict[str, object] = {}
        self.api_key_routes = {"key_linux": "ext_linux"}
        self.busy_clients: set[str] = set()
        self.connection_changes: list[tuple[str, bool]] = []

    async def save(self) -> None:
        return None

    async def set_connection_enabled(self, client_id: str, enabled: bool):
        item = self.clients[client_id]
        item.connection_enabled = bool(enabled)
        self.connection_changes.append((client_id, bool(enabled)))
        if not enabled:
            self.sockets.pop(client_id, None)
        return item

    def online_client_ids(self) -> list[str]:
        return [
            client_id
            for client_id in self.sockets
            if self.clients.get(client_id) and self.clients[client_id].connection_enabled
        ]

    def resolve_client(self, requested: str | None) -> str:
        if requested and requested in self.online_client_ids():
            return requested
        raise ConnectionError("offline")

    def summaries(self):
        return [
            {
                "client_id": client_id,
                "connection_enabled": item.connection_enabled,
                "online": client_id in self.sockets and item.connection_enabled,
                "busy": False,
                "metadata": {},
            }
            for client_id, item in self.clients.items()
        ]


def make_app(monkeypatch) -> FastAPI:
    app = FastAPI()
    app.state.admin_sessions = Sessions()
    app.state.linux_workers = Workers()
    app.state.registry = Registry()

    async def should_not_control(*args, **kwargs):
        raise AssertionError("offline Worker must not require extension control")

    monkeypatch.setattr(enable_patch, "_request_extension_control", should_not_control)
    monkeypatch.setattr(authority_patch, "_request_extension_control", should_not_control)
    install_linux_worker_enable_patch(app)
    install_worker_disable_authority_patch(app)
    return app


def test_linux_worker_page_can_disable_offline_worker_and_reenable_it(monkeypatch) -> None:
    app = make_app(monkeypatch)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    disabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    payload = disabled.json()
    assert payload["enabled"] is False
    assert payload["connection_enabled"] is False
    assert payload["control"]["skipped"] is True
    assert payload["control"]["reason"] == "offline-no-control"
    assert app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] is False
    assert app.state.registry.clients["ext_linux"].connection_enabled is False
    assert app.state.registry.api_key_routes == {}

    # A background compatibility path cannot reopen the transport while the
    # durable Worker switch remains disabled.
    background = asyncio.run(app.state.registry.set_connection_enabled("ext_linux", True))
    assert background.connection_enabled is False
    assert app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] is False

    enabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    enabled_payload = enabled.json()
    assert enabled_payload["enabled"] is True
    assert enabled_payload["connection_enabled"] is True
    assert enabled_payload["reconnect_pending"] is True
    assert app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] is True
    assert app.state.registry.clients["ext_linux"].connection_enabled is True


def test_linux_worker_page_can_disable_even_without_bound_extension(monkeypatch) -> None:
    app = make_app(monkeypatch)
    worker = app.state.linux_workers.data["workers"]["wrk_linux"]
    worker["extension_client_id"] = ""
    worker["metadata"] = {"bridge": {}}
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    disabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    payload = disabled.json()
    assert payload["enabled"] is False
    assert payload["control"]["skipped"] is True
    assert payload["control"]["reason"] == "no-bound-extension"
    assert worker["enabled"] is False


def test_linux_worker_enable_patch_is_aligned_to_v02242() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "linux_worker_enable_patch.py").read_text(encoding="utf-8")
    assert 'PATCH_VERSION = "0.22.42"' in source
    assert 'control = skipped_control("offline-no-control")' in source
    assert "persist_switch(worker_id, True)" in source
