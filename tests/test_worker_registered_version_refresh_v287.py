from __future__ import annotations

import asyncio

from app.registry import ClientRegistry


def test_paired_worker_manifest_version_updates_registered_version_and_persists(tmp_path):
    async def scenario():
        registry = ClientRegistry(tmp_path)
        client_id, _token = await registry.register(
            name="Linux-free",
            browser_name="Chrome",
            version="0.22.108",
            metadata={"runtime_id": "existing-installation"},
        )
        assert registry.clients[client_id].version == "0.22.108"
        await registry.touch(client_id, {
            "runtime_id": "existing-installation",
            "extension_version": "0.22.287",
        })
        assert registry.clients[client_id].version == "0.22.287"
        reloaded = ClientRegistry(tmp_path)
        await reloaded.load()
        assert reloaded.clients[client_id].version == "0.22.287"
        assert reloaded.clients[client_id].metadata["extension_version"] == "0.22.287"

        # Normal status/heartbeat messages without a manifest version cannot
        # alter it, nor can junk masquerading as a manifest value.
        await registry.touch(client_id, {"account_type": "paid"})
        await registry.touch(client_id, {"extension_version": "not-a-version"})
        await registry.touch(client_id, {"extension_version": "0.22.287 with secrets"})
        assert registry.clients[client_id].version == "0.22.287"

    asyncio.run(scenario())


def test_register_version_does_not_change_without_authenticated_metadata(tmp_path):
    async def scenario():
        registry = ClientRegistry(tmp_path)
        client_id, _token = await registry.register(
            name="win-free", browser_name="Chrome",
            version="0.22.101", metadata={},
        )
        await registry.touch(client_id, {})
        await registry.touch("ext_nonexistent", {"extension_version": "0.22.287"})
        assert registry.clients[client_id].version == "0.22.101"

    asyncio.run(scenario())
