"""Regression coverage for quota-aware Worker routing and repeated account-limit rejection."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.capacity_scheduler_v58 import install_capacity_scheduler_v58
from app.model_capability_routing_patch import MINI_MODEL, _idle_compatible
from app.registry import ClientRegistry


def _registry(tmp_path):
    registry = ClientRegistry(tmp_path)
    registry.clients = {
        "win-free": SimpleNamespace(connection_enabled=True, metadata={"account_type": "free"}),
        "linux": SimpleNamespace(connection_enabled=True, metadata={"account_type": "free"}),
    }
    registry.sockets = {"win-free": object(), "linux": object()}
    registry.chatgpt_routing_ready = lambda _: True
    registry.client_models = lambda _: [{"id": MINI_MODEL}]
    registry.rate_limit_remaining_seconds = lambda client_id: 600.0 if client_id == "win-free" else 0.0
    return registry


def test_automatic_route_abandons_sticky_quota_limited_worker(tmp_path):
    registry = _registry(tmp_path)
    registry.routing_key_context.set("bot2")
    registry.api_key_routes["bot2"] = "win-free"
    selected = registry.resolve_client(None)
    assert selected == "linux"
    assert registry.api_key_routes["bot2"] == "linux"
    with pytest.raises(ConnectionError, match="rate limited"):
        registry.resolve_client("win-free")


def test_mini_capability_route_excludes_quota_cooling_free_worker(tmp_path):
    registry = _registry(tmp_path)
    assert _idle_compatible(registry, MINI_MODEL) == ["linux"]
    registry.rate_limit_remaining_seconds = lambda _: 600.0
    with pytest.raises(ConnectionError, match="rate limited"):
        _idle_compatible(registry, MINI_MODEL)
    with pytest.raises(ConnectionError, match="rate limited"):
        registry.resolve_client(None)


def test_rate_limit_rejections_escalate_and_success_resets(tmp_path):
    app = FastAPI()

    async def publish(_request_id, _event):
        return True

    async def release(_request_id):
        return None

    async def send(_client_id, _message):
        return None

    broker = SimpleNamespace(
        requests={},
        client_requests={},
        client_active_requests={},
        release=release,
        publish=publish,
    )
    registry = SimpleNamespace(
        send=send,
        summaries=lambda: [],
        online_client_ids=lambda: ["win-free"],
        clients={"win-free": object()},
        routing_key_context=ContextVar("quota-test-route", default=None),
    )
    app.state.settings = SimpleNamespace(data_dir=tmp_path)
    app.state.broker = broker
    app.state.registry = registry
    install_capacity_scheduler_v58(app)
    circuit = app.state.capacity_scheduler_v58_runtime

    async def simulate():
        event = {
            "type": "chat.error",
            "error": "ChatGPT is temporarily rate limited; request dispatch paused for 300s",
        }
        first = SimpleNamespace(client_id="win-free", diagnostics={})
        broker.requests["req1"] = first
        await broker.publish("req1", event)
        assert first.diagnostics["rate_limit_circuit_strikes"] == 1
        assert first.diagnostics["rate_limit_circuit_pause_seconds"] == 300
        await broker.publish("req1", event)  # repeated terminal must not double-count
        assert circuit["rate_limit_strikes"]["win-free"] == 1

        second = SimpleNamespace(client_id="win-free", diagnostics={})
        broker.requests["req2"] = second
        await broker.publish("req2", event)
        assert second.diagnostics["rate_limit_circuit_strikes"] == 2
        assert second.diagnostics["rate_limit_circuit_pause_seconds"] == 600
        assert registry.rate_limit_remaining_seconds("win-free") > 550

        third = SimpleNamespace(client_id="win-free", diagnostics={})
        broker.requests["req3"] = third
        await broker.publish("req3", {"type": "chat.completed", "text": "ok"})
        assert registry.rate_limit_remaining_seconds("win-free") == 0
        assert "win-free" not in circuit["rate_limit_strikes"]

    asyncio.run(simulate())

def test_worker_model_catalog_autosync_vm() -> None:
    import shutil
    import subprocess
    from pathlib import Path

    if not shutil.which("node"):
        pytest.skip("node is unavailable")
    path = Path(__file__).resolve().parents[1] / "tests" / "worker_model_autosync_v131.mjs"
    process = subprocess.run(["node", str(path)], capture_output=True, text=True, check=False, timeout=30)
    assert process.returncode == 0, process.stdout + process.stderr
