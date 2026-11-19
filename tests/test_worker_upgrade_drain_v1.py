from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

from fastapi import FastAPI

from app.worker_upgrade_drain_patch import install_worker_upgrade_drain_patch


def test_upgrade_command_waits_for_all_same_host_requests_before_restarting(tmp_path) -> None:
    workers = {
        "wrk_primary": {
            "worker_id": "wrk_primary",
            "platform": "linux",
            "hostname": "ubuntu-worker",
            "device_id": "device-1",
            "extension_client_id": "ext_primary",
            "metadata": {},
        },
        "wrk_slot2": {
            "worker_id": "wrk_slot2",
            "platform": "linux",
            "hostname": "ubuntu-worker",
            "device_id": "device-1",
            "extension_client_id": "ext_slot2",
            "metadata": {},
        },
    }
    store = SimpleNamespace(data={"workers": workers}, _lock=threading.RLock())
    registry = SimpleNamespace(
        clients={
            "ext_primary": SimpleNamespace(metadata={}),
            "ext_slot2": SimpleNamespace(metadata={}),
        },
        chatgpt_routing_ready=lambda client_id: True,
        online_client_ids=lambda: ["ext_primary", "ext_slot2"],
    )
    broker = SimpleNamespace(
        client_active_requests={
            "ext_primary": {"req_primary": 1},
            "ext_slot2": {"req_slot2": 1},
        },
        client_requests={},
        requests={},
    )
    sent: list[tuple[str, str]] = []

    async def send(worker_id, command, arguments, *, wait, timeout):
        sent.append((worker_id, command))
        return {"accepted": True, "result": {"ok": True, "scheduled": True}}

    app = FastAPI()
    app.state.linux_workers = store
    app.state.registry = registry
    app.state.broker = broker
    app.state.send_linux_worker_command = send
    app.state.worker_upgrade_drain_timeout_seconds = 2.0
    app.state.worker_upgrade_drain_settle_seconds = 0.05
    install_worker_upgrade_drain_patch(app)

    async def scenario():
        task = asyncio.create_task(
            app.state.send_linux_worker_command(
                "wrk_primary", "upgrade_worker", {}, wait=True, timeout=20
            )
        )
        await asyncio.sleep(0.05)

        assert registry.chatgpt_routing_ready("ext_primary") is False
        assert registry.chatgpt_routing_ready("ext_slot2") is False
        assert registry.online_client_ids() == []
        assert sent == []

        broker.client_active_requests["ext_primary"] = {}
        broker.client_active_requests["ext_slot2"] = {}
        result = await task

        assert result["result"]["ok"] is True
        assert sent == [("wrk_primary", "upgrade_worker")]
        assert registry.chatgpt_routing_ready("ext_primary") is False
        assert registry.chatgpt_routing_ready("ext_slot2") is False

        app.state.clear_worker_upgrade_drain("wrk_primary")
        assert registry.chatgpt_routing_ready("ext_primary") is True
        assert registry.chatgpt_routing_ready("ext_slot2") is True

    asyncio.run(scenario())


def test_upgrade_drain_timeout_does_not_restart_worker_and_releases_routing() -> None:
    store = SimpleNamespace(
        data={
            "workers": {
                "wrk_test": {
                    "worker_id": "wrk_test",
                    "platform": "linux",
                    "hostname": "ubuntu-worker",
                    "extension_client_id": "ext_test",
                    "metadata": {},
                }
            }
        },
        _lock=threading.RLock(),
    )
    registry = SimpleNamespace(
        clients={"ext_test": SimpleNamespace(metadata={})},
        chatgpt_routing_ready=lambda client_id: True,
        online_client_ids=lambda: ["ext_test"],
    )
    broker = SimpleNamespace(client_active_requests={"ext_test": {"req_active": 1}})
    sent: list[str] = []

    async def send(worker_id, command, arguments, *, wait, timeout):
        sent.append(command)
        return {"accepted": True, "result": {"ok": True, "scheduled": True}}

    app = FastAPI()
    app.state.linux_workers = store
    app.state.registry = registry
    app.state.broker = broker
    app.state.send_linux_worker_command = send
    app.state.worker_upgrade_drain_timeout_seconds = 0.1
    app.state.worker_upgrade_drain_settle_seconds = 0.0
    install_worker_upgrade_drain_patch(app)

    async def scenario():
        result = await app.state.send_linux_worker_command(
            "wrk_test", "upgrade_worker", {}, wait=True, timeout=20
        )
        assert result["accepted"] is False
        assert result["result"]["error"] == "worker_active_requests_drain_timeout"
        assert sent == []
        assert registry.chatgpt_routing_ready("ext_test") is True

    asyncio.run(scenario())


def test_entry_installs_upgrade_drain_before_server_worker_auto_sync() -> None:
    from pathlib import Path

    entry = Path("app/entry.py").read_text(encoding="utf-8")
    assert "install_worker_upgrade_drain_patch(app)" in entry
    assert entry.index("install_linux_worker_upgrade_patch(app)") < entry.index(
        "install_worker_upgrade_drain_patch(app)"
    )
    assert entry.index("install_worker_upgrade_drain_patch(app)") < entry.index(
        "install_server_worker_sync_patch(app)"
    )
