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
        assert state.diagnostics["request_stall_guard"] == "request-stall-v38"
        assert any(payload.get("type") == "chat.cancel" for _client, payload in registry.sent)
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_extension_activity_cancels_dispatch_watchdog(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 0.2)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 0.2)
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


def test_post_submit_without_generation_fails_and_force_releases(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 0.03)
        monkeypatch.setattr(request_stall_patch, "ORPHAN_RELEASE_GRACE_SECONDS", 0.02)
        _app, broker, registry = make_app()
        state = await broker.create("req_post_submit_stall", "ext_post_submit")
        await broker.publish(
            state.request_id,
            {
                "type": "chat.diagnostics",
                "request_id": state.request_id,
                "diagnostics": {
                    "submit_stage": "confirmed",
                    "submission_confirmed": True,
                    "generating_observed": False,
                },
            },
        )

        with pytest.raises(RuntimeError, match="response generation did not start"):
            await asyncio.wait_for(state.final_future, timeout=0.2)

        assert state.diagnostics["post_submit_generation_watchdog_fired"] is True
        assert any(payload.get("type") == "chat.cancel" for _client, payload in registry.sent)
        await asyncio.sleep(0.05)
        assert state.request_id not in broker.requests
        assert broker.capacity_snapshot("ext_post_submit")["used_units"] == 0

    asyncio.run(scenario())


def test_generation_start_prevents_post_submit_false_positive(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 0.03)
        _app, broker, _registry = make_app()
        state = await broker.create("req_generation_started", "ext_generation")
        await broker.publish(
            state.request_id,
            {
                "type": "chat.diagnostics",
                "request_id": state.request_id,
                "diagnostics": {"submit_stage": "confirmed", "submission_confirmed": True},
            },
        )
        await asyncio.sleep(0.01)
        await broker.publish(state.request_id, {"type": "chat.started", "request_id": state.request_id})
        await asyncio.sleep(0.05)

        assert state.final_future is not None and not state.final_future.done()
        assert state.diagnostics.get("extension_generation_started_ms") is not None
        assert state.diagnostics.get("post_submit_generation_watchdog_fired") is not True
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_absolute_request_lease_reclaims_abandoned_handler(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "ABSOLUTE_REQUEST_TIMEOUT_GRACE_SECONDS", -0.97)
        monkeypatch.setattr(request_stall_patch, "ORPHAN_RELEASE_GRACE_SECONDS", 0.02)
        _app, broker, registry = make_app()
        state = await broker.create("req_absolute_lease", "ext_lease")
        await registry.send(
            "ext_lease",
            {
                "type": "chat.request",
                "request_id": state.request_id,
                "options": {"timeout_seconds": 1},
            },
        )

        with pytest.raises(RuntimeError, match="browser lease"):
            await asyncio.wait_for(state.final_future, timeout=0.2)
        assert state.diagnostics["absolute_request_lease_watchdog_fired"] is True
        await asyncio.sleep(0.05)
        assert state.request_id not in broker.requests
        assert broker.capacity_snapshot("ext_lease")["used_units"] == 0

    asyncio.run(scenario())


def test_active_request_details_expose_stage_and_age(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 1.0)
        _app, broker, registry = make_app()
        registry.sockets["ext_state"] = object()
        state = await broker.create("req_state", "ext_state")
        await broker.publish(
            state.request_id,
            {
                "type": "chat.diagnostics",
                "request_id": state.request_id,
                "diagnostics": {"submit_stage": "confirmed", "submission_confirmed": True},
            },
        )
        rows = registry.summaries()
        details = rows[0]["active_request_details"]
        assert rows[0]["active_api_calls"] == 1
        assert details[0]["request_id"] == "req_state"
        assert details[0]["stage"] == "submitted_waiting_generation"
        assert details[0]["last_event_type"] == "chat.diagnostics"
        assert details[0]["age_seconds"] >= 0
        await broker.release(state.request_id)

    asyncio.run(scenario())


def test_stale_replaced_socket_cannot_fail_new_connection_request(monkeypatch):
    async def scenario() -> None:
        monkeypatch.setattr(request_stall_patch, "DISPATCH_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "SUBMIT_ACK_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(request_stall_patch, "POST_SUBMIT_START_TIMEOUT_SECONDS", 1.0)
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
