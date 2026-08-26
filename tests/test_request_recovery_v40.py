from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.broker import RequestBroker
from app import request_recovery_patch, request_stall_patch


class FakeRegistry:
    def __init__(self) -> None:
        self.sockets = {}
        self.sent: list[tuple[str, dict]] = []

    async def send(self, client_id: str, payload: dict) -> None:
        self.sent.append((client_id, dict(payload)))

    async def attach(self, client_id: str, websocket) -> None:
        self.sockets[client_id] = websocket

    async def detach(self, client_id: str, websocket) -> None:
        if self.sockets.get(client_id) is websocket:
            self.sockets.pop(client_id, None)

    def summaries(self) -> list[dict]:
        return [{"client_id": client_id} for client_id in sorted(self.sockets)]


def make_app():
    broker = RequestBroker()
    broker.client_active_requests = {}

    async def create_concurrent(request_id: str, client_id: str):
        state = await RequestBroker.create(broker, request_id, client_id)
        broker.client_active_requests.setdefault(client_id, {})[request_id] = 1
        return state

    async def release_concurrent(request_id: str) -> None:
        state = broker.requests.get(request_id)
        if state is not None:
            active = broker.client_active_requests.get(state.client_id, {})
            active.pop(request_id, None)
            if not active:
                broker.client_active_requests.pop(state.client_id, None)
        await RequestBroker.release(broker, request_id)

    def capacity_snapshot(client_id: str) -> dict:
        active = broker.client_active_requests.get(client_id, {})
        return {
            "used_units": len(active),
            "active_requests": len(active),
            "request_weights": dict(active),
        }

    broker.create = create_concurrent
    broker.release = release_concurrent
    broker.capacity_snapshot = capacity_snapshot
    registry = FakeRegistry()
    app = FastAPI()
    app.state.broker = broker
    app.state.registry = registry
    request_stall_patch.install_request_stall_patch(app)
    request_recovery_patch.install_request_recovery_patch(app)
    return app, broker, registry


def test_terminal_error_force_releases_capacity_when_handler_never_releases(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 10.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 10.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 10.0)
        monkeypatch.setattr(request_recovery_patch, "TERMINAL_RELEASE_GRACE_SECONDS", 0.01)
        _app, broker, _registry = make_app()
        state = await broker.create("req_terminal_leak", "ext_terminal_leak")

        await broker.publish(
            state.request_id,
            {"type": "chat.error", "request_id": state.request_id, "error": "browser failed"},
        )
        with pytest.raises(RuntimeError, match="browser failed"):
            await state.final_future

        await asyncio.sleep(0.04)
        assert state.request_id not in broker.requests
        assert broker.capacity_snapshot("ext_terminal_leak")["used_units"] == 0
        assert state.diagnostics["request_recovery_patch"] == "request-recovery-v40"
        assert state.diagnostics["terminal_release_scheduled"] is True

    asyncio.run(scenario())


def test_terminal_reaper_reclaims_preexisting_terminal_state(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 10.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 10.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 10.0)
        monkeypatch.setattr(request_recovery_patch, "TERMINAL_RELEASE_GRACE_SECONDS", 60.0)
        monkeypatch.setattr(request_recovery_patch, "TERMINAL_REAPER_STALE_SECONDS", 0.0)
        _app, broker, _registry = make_app()
        state = await broker.create("req_old_terminal", "ext_old_terminal")
        await broker.publish(
            state.request_id,
            {"type": "chat.error", "request_id": state.request_id, "error": "old failure"},
        )
        with pytest.raises(RuntimeError, match="old failure"):
            await state.final_future

        assert broker.capacity_snapshot("ext_old_terminal")["used_units"] == 1
        reclaimed = await broker.request_recovery_reap_once()
        assert reclaimed == 1
        assert state.request_id not in broker.requests
        assert broker.capacity_snapshot("ext_old_terminal")["used_units"] == 0
        assert state.diagnostics["terminal_reaper_fired"] is True

    asyncio.run(scenario())


def test_recovery_patch_is_installed_after_stall_guard():
    from pathlib import Path

    entry = (Path(__file__).resolve().parents[1] / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_request_recovery_patch(app)" in entry
    assert entry.index("install_request_stall_patch(app)") < entry.index("install_request_recovery_patch(app)")
