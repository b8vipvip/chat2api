from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi import FastAPI

from app import model_capability_routing_patch as routing
from app.linux_worker_registry import ClientRegistry
from app.worker_model_tasks_v147_patch import install_manual_worker_text_model_tasks_v147_patch


def test_v147_touch_preserves_websocket_identity_and_boolean_contract(monkeypatch, tmp_path):
    async def scenario():
        app = FastAPI()

        @app.get("/api/admin/overview")
        async def admin_overview():
            return {}

        registry = ClientRegistry(tmp_path)
        app.state.registry = registry
        app.state.settings = SimpleNamespace(data_dir=tmp_path)
        app.state.worker_text_model_management_v146_snapshot = lambda: {"disabled": []}

        monkeypatch.setattr(routing, "_compatible", routing._compatible)
        install_manual_worker_text_model_tasks_v147_patch(app)

        client_id, _token = await registry.register(
            "Worker", "Chrome", "0.22.291", {}
        )
        current_socket = object()
        stale_socket = object()
        await registry.attach(client_id, current_socket)

        assert await registry.touch(
            client_id, None, websocket=current_socket
        ) is True
        assert await registry.touch(
            client_id, {"extension_version": "0.22.292"}, websocket=current_socket
        ) is True
        assert registry.clients[client_id].version == "0.22.292"

        # A stale/replaced socket must remain rejected by the wrapper too.
        assert await registry.touch(
            client_id, {"extension_version": "0.0.0"}, websocket=stale_socket
        ) is False
        assert registry.clients[client_id].version == "0.22.292"

    asyncio.run(scenario())
