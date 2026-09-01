from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.worker_disable_authority_patch as authority_patch
from app.admin_auth import SESSION_COOKIE
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
                    "metadata": {
                        "bridge": {
                            "extension_id": "ext_linux",
                            "connection_enabled": True,
                            "online": True,
                        }
                    },
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
            "ext_linux": SimpleNamespace(
                client_id="ext_linux",
                connection_enabled=True,
                metadata={},
            ),
            "ext_windows": SimpleNamespace(
                client_id="ext_windows",
                connection_enabled=True,
                metadata={},
            ),
        }
        self.sockets = {"ext_linux": object(), "ext_windows": object()}
        self.api_key_routes = {"key_linux": "ext_linux", "key_windows": "ext_windows"}
        self.saved = 0
        self.connection_changes: list[tuple[str, bool]] = []

    async def save(self) -> None:
        self.saved += 1

    async def set_connection_enabled(self, client_id: str, enabled: bool):
        item = self.clients[client_id]
        item.connection_enabled = bool(enabled)
        self.connection_changes.append((client_id, bool(enabled)))
        if not enabled:
            self.sockets.pop(client_id, None)
        return item

    def summaries(self):
        return [
            {
                "client_id": client_id,
                "connection_enabled": item.connection_enabled,
                "online": client_id in self.sockets and item.connection_enabled,
                "busy": client_id == "ext_linux",
                "metadata": dict(item.metadata),
            }
            for client_id, item in self.clients.items()
        ]


def make_app(monkeypatch):
    app = FastAPI()
    app.state.admin_sessions = Sessions()
    app.state.linux_workers = Workers()
    app.state.registry = Registry()
    control_calls: list[tuple[str, str, dict]] = []

    async def fake_control(runtime, client_id, action, payload, *, timeout_seconds):
        assert runtime is app
        control_calls.append((client_id, action, dict(payload or {})))
        return {
            "ok": True,
            "action": action,
            "data": {
                "keep_windows": 1,
                "managed_windows_before": 3,
                "managed_windows_after": 1,
                "closed_window_ids": [102, 103],
            },
        }

    monkeypatch.setattr(authority_patch, "_request_extension_control", fake_control)

    @app.post("/api/workers/extension-binding-ticket")
    async def binding_ticket_fallback():
        return {"ok": True}

    install_worker_disable_authority_patch(app)
    return app, control_calls


def test_worker_management_disable_and_enable_share_linux_worker_authority(monkeypatch) -> None:
    app, control_calls = make_app(monkeypatch)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    disabled = client.post("/api/admin/extensions/ext_linux/disconnect")
    assert disabled.status_code == 200, disabled.text
    payload = disabled.json()
    assert payload["enabled"] is False
    assert payload["state"] == "disabled"
    assert payload["connection_enabled"] is False
    assert control_calls == [("ext_linux", "worker.disable", {"keep_windows": 1})]
    assert app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] is False
    assert app.state.registry.clients["ext_linux"].connection_enabled is False
    assert app.state.registry.api_key_routes == {"key_windows": "ext_windows"}

    rows = {row["client_id"]: row for row in app.state.registry.summaries()}
    assert rows["ext_linux"]["admin_enabled"] is False
    assert rows["ext_linux"]["connection_enabled"] is False
    assert rows["ext_linux"]["online"] is False
    assert rows["ext_linux"]["busy"] is False

    enabled = client.post("/api/admin/extensions/ext_linux/enable")
    assert enabled.status_code == 200, enabled.text
    enabled_payload = enabled.json()
    assert enabled_payload["enabled"] is True
    assert enabled_payload["state"] == "enabled"
    assert enabled_payload["connection_enabled"] is True
    assert enabled_payload["reconnect_pending"] is True
    assert app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] is True
    assert app.state.registry.clients["ext_linux"].connection_enabled is True


def test_background_registry_save_cannot_revive_disabled_linux_worker(monkeypatch) -> None:
    app, _ = make_app(monkeypatch)
    authority = app.state.worker_disable_authority

    asyncio.run(authority["disable_client"]("ext_linux"))
    worker = app.state.linux_workers.data["workers"]["wrk_linux"]
    assert worker["enabled"] is False

    app.state.registry.clients["ext_linux"].connection_enabled = True
    app.state.registry.sockets["ext_linux"] = object()
    asyncio.run(app.state.registry.save())

    assert app.state.registry.clients["ext_linux"].connection_enabled is False
    row = next(row for row in app.state.registry.summaries() if row["client_id"] == "ext_linux")
    assert row["admin_enabled"] is False
    assert row["online"] is False


def test_background_registry_set_true_cannot_revive_disabled_linux_worker(monkeypatch) -> None:
    app, _ = make_app(monkeypatch)
    authority = app.state.worker_disable_authority

    asyncio.run(authority["disable_client"]("ext_linux"))
    worker = app.state.linux_workers.data["workers"]["wrk_linux"]
    assert worker["enabled"] is False
    assert app.state.registry.clients["ext_linux"].connection_enabled is False

    # Simulate a stale physical connection appearing after the durable disable.
    # Rejecting a background True must drive the real false setter so the stale
    # socket is closed, not merely hidden from routing summaries.
    app.state.registry.sockets["ext_linux"] = object()
    result = asyncio.run(app.state.registry.set_connection_enabled("ext_linux", True))

    assert result.connection_enabled is False
    assert worker["enabled"] is False
    assert app.state.registry.clients["ext_linux"].connection_enabled is False
    assert "ext_linux" not in app.state.registry.sockets
    assert ("ext_linux", True) not in app.state.registry.connection_changes
    assert app.state.registry.connection_changes[-1] == ("ext_linux", False)

    enabled = asyncio.run(authority["enable_client"]("ext_linux"))
    assert enabled["enabled"] is True
    assert worker["enabled"] is True
    assert app.state.registry.clients["ext_linux"].connection_enabled is True
    assert ("ext_linux", True) in app.state.registry.connection_changes


def test_offline_worker_can_still_be_disabled_persistently(monkeypatch) -> None:
    app, control_calls = make_app(monkeypatch)
    app.state.registry.sockets.pop("ext_linux", None)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    response = client.post("/api/admin/extensions/ext_linux/disconnect")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["enabled"] is False
    assert payload["control"]["skipped"] is True
    assert payload["control"]["reason"] == "offline-no-control"
    assert control_calls == []
    assert app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] is False
    assert app.state.registry.clients["ext_linux"].connection_enabled is False


def test_disabled_worker_cannot_issue_new_extension_binding_ticket(monkeypatch) -> None:
    app, _ = make_app(monkeypatch)
    app.state.linux_workers.data["workers"]["wrk_linux"]["enabled"] = False
    client = TestClient(app)

    response = client.post(
        "/api/workers/extension-binding-ticket",
        headers={"x-worker-id": "wrk_linux"},
    )
    assert response.status_code == 409, response.text
    assert response.json()["state"] == "disabled"


def test_authority_ui_uses_enable_disable_terms_without_global_dom_observer() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    source = (root / "app" / "admin_worker_disable_authority_v62.js").read_text(encoding="utf-8")
    linux_source = (root / "app" / "admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")
    entry = (root / "app" / "entry.py").read_text(encoding="utf-8")
    patch = (root / "app" / "worker_disable_authority_patch.py").read_text(encoding="utf-8")

    for token in ("禁用", "启用", "已禁用", "已启用"):
        assert token in source
    assert 'observer.observe(document.documentElement' not in source
    assert 'observer.observe(body, { childList: true, subtree: false })' in source
    assert 'const nextText = isEnabled ? "禁用" : "启用";' in linux_source
    assert "Do not treat every registry setter call as administrator intent" in patch
    assert "install_worker_disable_authority_patch(app)" in entry
    assert entry.rindex("install_worker_disable_authority_patch(app)") > entry.rindex("install_server_worker_sync_patch(app)")