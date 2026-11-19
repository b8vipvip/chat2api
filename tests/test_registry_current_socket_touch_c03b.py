from __future__ import annotations

import asyncio
from pathlib import Path

from app.registry import ClientRegistry, PersistedClient


def test_replaced_worker_socket_cannot_refresh_capacity_or_identity_metadata(tmp_path: Path) -> None:
    async def scenario() -> None:
        registry = ClientRegistry(tmp_path)
        client = PersistedClient(
            client_id="worker-a",
            name="Worker A",
            browser_name="Chrome",
            version="1.0.0",
            token_hash="test-hash",
            created_at="2026-01-01T00:00:00+00:00",
            last_seen_at="before-replacement",
            metadata={"device_id": "device-current"},
            device_id="device-current",
        )
        registry.clients[client.client_id] = client
        current_socket = object()
        stale_socket = object()
        registry.sockets[client.client_id] = current_socket

        accepted = await registry.touch(
            client.client_id,
            {
                "extension_version": "9.9.9",
                "device_id": "device-stale",
                "worker_pool_admission_revision": 224,
            },
            websocket=stale_socket,
        )

        assert accepted is False
        assert client.last_seen_at == "before-replacement"
        assert client.version == "1.0.0"
        assert client.device_id == "device-current"
        assert client.metadata == {"device_id": "device-current"}
        assert client.client_id not in registry.worker_pool_capacity_received_at
        assert not registry.path.exists()

        accepted = await registry.touch(
            client.client_id,
            {
                "extension_version": "2.1.3",
                "device_id": "device-current-2",
                "worker_pool_admission_revision": 224,
            },
            websocket=current_socket,
        )

        assert accepted is True
        assert client.version == "2.1.3"
        assert client.device_id == "device-current-2"
        assert client.metadata["device_id"] == "device-current-2"
        assert client.client_id in registry.worker_pool_capacity_received_at
        assert registry.path.exists()

    asyncio.run(scenario())


def test_extension_socket_checks_current_identity_when_touching_worker_metadata() -> None:
    source = (Path(__file__).resolve().parents[1] / "app/main.py").read_text(encoding="utf-8")
    assert "registry.touch(client_id, metadata, websocket=websocket)" in source
    assert "if not await registry.touch(client_id, metadata, websocket=websocket):" in source
