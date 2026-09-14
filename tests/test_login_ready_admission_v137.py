from __future__ import annotations

from copy import deepcopy
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.model_capability_routing_patch import _compatible
from app.registry import ClientRegistry, PersistedClient
from app.window_manager_v88_patch import install_window_manager_v88_patch


def _client(client_id: str, *, state: str, composer: bool) -> PersistedClient:
    return PersistedClient(
        client_id=client_id,
        name=client_id,
        browser_name="Chrome",
        version="0.8.39",
        token_hash="x",
        created_at="2026-09-13T20:00:00+08:00",
        metadata={
            "extension_version": "0.8.39",
            "account_type": "free",
            "chatgpt_login_state": state,
            "chatgpt_login_composer_ready": composer,
            "models": [{"id": "gpt-5.5-mini"}],
        },
    )


def test_registry_routes_only_confirmed_logged_in_workers(tmp_path) -> None:
    registry = ClientRegistry(tmp_path)
    ready = _client("ext_ready", state="ready", composer=True)
    logged_out = _client("ext_logged_out", state="login_required", composer=False)
    unknown = _client("ext_unknown", state="unknown", composer=False)
    registry.clients = {item.client_id: item for item in (ready, logged_out, unknown)}
    registry.sockets = {item.client_id: object() for item in (ready, logged_out, unknown)}

    assert registry.chatgpt_routing_ready("ext_ready") is True
    assert registry.chatgpt_routing_ready("ext_logged_out") is False
    assert registry.chatgpt_routing_ready("ext_unknown") is False

    registry.set_routing_key("key_qnbot")
    registry.api_key_routes["key_qnbot"] = "ext_logged_out"
    assert registry.resolve_client(None) == "ext_ready"
    assert registry.api_key_routes["key_qnbot"] == "ext_ready"

    with pytest.raises(ConnectionError, match="ChatGPT is not logged in"):
        registry.resolve_client("ext_logged_out")

    assert _compatible(registry, "ext_ready", "gpt-5.5-mini") is True
    assert _compatible(registry, "ext_logged_out", "gpt-5.5-mini") is False
    assert _compatible(registry, "ext_unknown", "gpt-5.5-mini") is False

    mini = next(item for item in registry.model_catalog(online_only=True) if item["id"] == "gpt-5.5-mini")
    assert mini["clients"] == ["ext_ready"]


def test_registry_fails_closed_when_only_transport_online_worker_is_logged_out(tmp_path) -> None:
    registry = ClientRegistry(tmp_path)
    logged_out = _client("ext_logged_out", state="login_required", composer=False)
    registry.clients = {logged_out.client_id: logged_out}
    registry.sockets = {logged_out.client_id: object()}
    assert registry.online_client_ids() == ["ext_logged_out"]
    with pytest.raises(ConnectionError, match="No ChatGPT-ready Chrome extension"):
        registry.resolve_client(None)


class _Sessions:
    def authenticate(self, _cookie) -> bool:
        return True


class _WindowRegistry:
    def __init__(self) -> None:
        self.clients = {"ext_ready": object(), "ext_logged_out": object()}
        self.sockets = dict(self.clients)
        self.rows = [
            self._row("ext_ready", "ready", True, 11),
            self._row("ext_logged_out", "login_required", False, 22),
        ]

    @staticmethod
    def _row(client_id: str, state: str, composer: bool, window_id: int) -> dict:
        return {
            "client_id": client_id,
            "device_name": "TX03",
            "online": True,
            "version": "0.8.39",
            "metadata": {
                "extension_version": "0.8.39",
                "chatgpt_login_state": state,
                "chatgpt_login_composer_ready": composer,
                "window_manager_revision": 90,
                "window_manager_v88": {
                    "updated_at_ms": 100,
                    "active": [{
                        "window_no": 1 if client_id == "ext_ready" else 2,
                        "window_id": window_id,
                        "tab_id": window_id + 100,
                        "status": "ready",
                        "opened_at_ms": 1000,
                    }],
                    "closed": [],
                },
            },
        }

    def summaries(self):
        return deepcopy(self.rows)

    async def send(self, client_id: str, payload: dict) -> None:
        assert payload["type"] == "window.manager.refresh"
        for row in self.rows:
            if row["client_id"] == client_id:
                row["metadata"]["window_manager_v88"]["updated_at_ms"] += 1


def test_window_manager_hides_logged_out_physical_windows() -> None:
    app = FastAPI()
    app.state.registry = _WindowRegistry()
    app.state.admin_sessions = _Sessions()
    install_window_manager_v88_patch(app)

    response = TestClient(app).get("/api/admin/window-manager")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert [row["client_id"] for row in payload["active"]] == ["ext_ready"]
    assert payload["truth"]["online_workers"] == 2
    assert payload["truth"]["verified_workers"] == 2
    assert payload["truth"]["reception_ready_workers"] == 1
    assert payload["truth"]["login_blocked_workers"] == 1
    assert payload["truth"]["login_blocked_active_rows_suppressed"] == 1
    blocked = next(row for row in payload["workers"] if row["client_id"] == "ext_logged_out")
    assert blocked["live_verified"] is True
    assert blocked["chatgpt_routing_ready"] is False
    assert blocked["reception_ready"] is False
    assert blocked["truth_status"] == "login-required"


def test_runtime_contract_advertises_v137_login_guard() -> None:
    from app.runtime_contract import SERVER_RUNTIME_VERSION, version_contract_payload
    payload = version_contract_payload(FastAPI(version=SERVER_RUNTIME_VERSION))
    assert payload["features"]["chatgpt_login_ready_admission_v137"] is True
    assert payload["features"]["window_manager_login_ready_filter_v137"] is True


class _Socket:
    def __init__(self) -> None:
        self.payloads = []

    async def send_json(self, payload) -> None:
        self.payloads.append(payload)


def test_generation_send_is_guarded_at_the_last_transport_boundary(tmp_path) -> None:
    registry = ClientRegistry(tmp_path)
    logged_out = _client("ext_logged_out", state="login_required", composer=False)
    registry.clients = {logged_out.client_id: logged_out}
    socket = _Socket()
    registry.sockets = {logged_out.client_id: socket}

    with pytest.raises(RuntimeError, match="ChatGPT is not logged in"):
        asyncio.run(registry.send("ext_logged_out", {"type": "chat.request", "request_id": "req_x"}))
    assert socket.payloads == []

    # Control traffic stays available so a transport-online Worker can still be
    # verified, remotely logged in and repaired while generation stays blocked.
    asyncio.run(registry.send("ext_logged_out", {"type": "window.manager.refresh"}))
    assert socket.payloads == [{"type": "window.manager.refresh"}]
