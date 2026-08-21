from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.broker import RequestBroker
from app import request_stall_patch


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


def make_app():
    broker = RequestBroker()
    registry = FakeRegistry()
    app = SimpleNamespace(state=SimpleNamespace(broker=broker, registry=registry))
    request_stall_patch.install_request_stall_patch(app)
    return app, broker, registry


def test_silent_extension_dispatch_fails_fast(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 0.2)
        _app, broker, registry = make_app()
        state = await broker.create("req_silent", "ext_silent")

        with pytest.raises(RuntimeError, match="did not acknowledge browser dispatch"):
            await asyncio.wait_for(state.final_future, timeout=0.2)

        assert state.diagnostics["extension_dispatch_watchdog_fired"] is True
        assert state.diagnostics["request_stall_guard"] == "request-stall-v34"
        assert any(payload.get("type") == "chat.cancel" for _client, payload in registry.sent)
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_extension_activity_cancels_dispatch_watchdog(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 0.2)
        _app, broker, _registry = make_app()
        state = await broker.create("req_ack", "ext_ack")
        await broker.publish(
            state.request_id,
            {
                "type": "chat.diagnostics",
                "request_id": state.request_id,
                "diagnostics": {"submit_stage": "confirmed", "submission_confirmed": True},
            },
        )
        await asyncio.sleep(0.06)
        assert state.final_future is not None and not state.final_future.done()
        assert state.diagnostics.get("extension_dispatch_ack_ms") is not None
        assert state.diagnostics.get("extension_submit_ack_ms") is not None
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_stale_replaced_socket_cannot_fail_new_connection_request(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
        _app, broker, registry = make_app()
        state = await broker.create("req_socket_race", "ext_race")
        old_socket = object()
        new_socket = object()
        old_ready = asyncio.Event()
        release_old = asyncio.Event()

        async def old_handler() -> None:
            await registry.attach("ext_race", old_socket)
            old_ready.set()
            await release_old.wait()
            await broker.publish(
                state.request_id,
                {
                    "type": "chat.error",
                    "request_id": state.request_id,
                    "error": "Chrome extension disconnected",
                },
            )
            await registry.detach("ext_race", old_socket)

        async def new_handler() -> None:
            await old_ready.wait()
            await registry.attach("ext_race", new_socket)
            await broker.publish(
                state.request_id,
                {
                    "type": "chat.diagnostics",
                    "request_id": state.request_id,
                    "diagnostics": {"submit_stage": "confirmed", "submission_confirmed": True},
                },
            )

        old_task = asyncio.create_task(old_handler())
        await new_handler()
        release_old.set()
        await old_task

        assert registry.sockets.get("ext_race") is new_socket
        assert state.final_future is not None and not state.final_future.done()
        assert state.diagnostics.get("stale_extension_disconnect_ignored") is True
        await broker.release(state.request_id)

    asyncio.run(scenario())
