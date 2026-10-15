from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.broker import RequestBroker
from app.worker_transport_recovery_patch import install_worker_transport_recovery_patch
import app.worker_transport_recovery_patch as recovery

ROOT = Path(__file__).resolve().parents[1]


def test_restart_does_not_preserve_a_request_just_because_socket_reconnected(monkeypatch):
    async def scenario():
        monkeypatch.setattr(recovery, "WORKER_RECONNECT_GRACE_SECONDS", .015)
        app = FastAPI()
        old_socket = object()
        fresh_socket = object()
        client = SimpleNamespace(metadata={"transport_runtime_epoch": "old-generation"})
        app.state.registry = SimpleNamespace(sockets={"ext_worker": old_socket},
                                             clients={"ext_worker": client})
        app.state.broker = RequestBroker()
        install_worker_transport_recovery_patch(app)
        state = await app.state.broker.create("req_restart", "ext_worker")
        async def restart():
            await asyncio.sleep(.003)
            app.state.registry.sockets["ext_worker"] = fresh_socket
            client.metadata["transport_runtime_epoch"] = "new-generation"
        updater = asyncio.create_task(restart())
        await app.state.broker.publish("req_restart", {
            "type": "chat.error", "request_id": "req_restart",
            "error": "Chrome extension disconnected",
            "_closed_socket": old_socket, "transport_runtime_epoch": "old-generation",
        })
        await updater
        with pytest.raises(RuntimeError, match="runtime restarted"):
            await state.final_future
        assert state.diagnostics["worker_runtime_restarted"] is True
        assert state.diagnostics["worker_transport_reconnected"] is False
    asyncio.run(scenario())


def test_same_runtime_socket_reconnect_may_preserve_request(monkeypatch):
    async def scenario():
        monkeypatch.setattr(recovery, "WORKER_RECONNECT_GRACE_SECONDS", .015)
        app = FastAPI()
        old_socket, fresh_socket = object(), object()
        client = SimpleNamespace(metadata={"transport_runtime_epoch": "same-generation"})
        app.state.registry = SimpleNamespace(sockets={"ext_worker": old_socket},
                                             clients={"ext_worker": client})
        app.state.broker = RequestBroker()
        install_worker_transport_recovery_patch(app)
        state = await app.state.broker.create("req_kept", "ext_worker")
        async def reconnect():
            await asyncio.sleep(.003)
            app.state.registry.sockets["ext_worker"] = fresh_socket
        updater = asyncio.create_task(reconnect())
        await app.state.broker.publish("req_kept", {
            "type": "chat.error", "request_id": "req_kept",
            "error": "Chrome extension disconnected",
            "_closed_socket": old_socket, "transport_runtime_epoch": "same-generation",
        })
        await updater
        assert not state.final_future.done()
        assert state.diagnostics["worker_transport_reconnected"] is True
        await app.state.broker.publish("req_kept", {"type": "chat.completed", "request_id": "req_kept", "text": "ok"})
        assert await state.final_future == "ok"
    asyncio.run(scenario())


def test_worker_epoch_is_bound_to_each_socket_and_not_a_stable_extension_id():
    js = (ROOT / "chrome_extension" / "background.js").read_text()
    server = (ROOT / "app" / "main.py").read_text()
    assert "const WORKER_RUNTIME_EPOCH = crypto.randomUUID()" in js
    assert "transport_runtime_epoch: WORKER_RUNTIME_EPOCH" in js
    assert 'if message_type == "extension.hello" and metadata:' in server
    assert '"_closed_socket": websocket' in server
