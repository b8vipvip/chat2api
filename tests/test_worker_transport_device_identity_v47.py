from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

import app.worker_transport_recovery_patch as transport_patch
from app.broker import RequestBroker
from app.request_device_identity_patch import install_request_device_identity_patch
from app.telemetry import TelemetryStore
from app.worker_transport_recovery_patch import install_worker_transport_recovery_patch


ROOT = Path(__file__).resolve().parents[1]


def test_brief_worker_disconnect_preserves_request_when_new_socket_reconnects(monkeypatch):
    async def scenario():
        monkeypatch.setattr(transport_patch, "WORKER_RECONNECT_GRACE_SECONDS", 0.03)
        app = FastAPI()
        app.state.broker = RequestBroker()
        app.state.registry = SimpleNamespace(sockets={})
        install_worker_transport_recovery_patch(app)

        state = await app.state.broker.create("req_reconnect", "ext_linux")
        old_socket = object()
        new_socket = object()
        app.state.registry.sockets["ext_linux"] = old_socket

        disconnect = asyncio.create_task(
            app.state.broker.publish(
                "req_reconnect",
                {"type": "chat.error", "request_id": "req_reconnect", "error": "Chrome extension disconnected"},
            )
        )
        await asyncio.sleep(0.005)
        app.state.registry.sockets["ext_linux"] = new_socket
        await app.state.broker.publish(
            "req_reconnect",
            {"type": "chat.completed", "request_id": "req_reconnect", "text": "recovered"},
        )

        assert await disconnect is True
        assert await state.final_future == "recovered"
        assert state.diagnostics["worker_transport_recovery"] == "worker-transport-recovery-v47"
        assert state.diagnostics.get("worker_disconnect_grace_expired") is not True

    asyncio.run(scenario())


def test_worker_disconnect_becomes_terminal_after_grace_without_reconnect(monkeypatch):
    async def scenario():
        monkeypatch.setattr(transport_patch, "WORKER_RECONNECT_GRACE_SECONDS", 0.005)
        app = FastAPI()
        app.state.broker = RequestBroker()
        app.state.registry = SimpleNamespace(sockets={"ext_linux": object()})
        install_worker_transport_recovery_patch(app)

        state = await app.state.broker.create("req_offline", "ext_linux")
        assert await app.state.broker.publish(
            "req_offline",
            {"type": "chat.error", "request_id": "req_offline", "error": "Chrome extension disconnected"},
        ) is True
        with pytest.raises(RuntimeError, match="Worker disconnected"):
            await state.final_future
        assert state.diagnostics["worker_disconnect_grace_expired"] is True

    asyncio.run(scenario())


def test_request_history_resolves_worker_to_device_code_name(tmp_path):
    async def scenario():
        telemetry = TelemetryStore(tmp_path)
        await telemetry.upsert(
            {
                "request_id": "req_device",
                "client_id": "ext_7HUtMOXF5Qq0",
                "status": "completed",
                "requested_model": "gpt-5.5-mini",
            }
        )

        class Pairings:
            path = tmp_path / "pairing_codes.json"

            @staticmethod
            def list_public():
                return [
                    {
                        "pairing_id": "pair_N9aNI5vAn4",
                        "name": "ubuntu03",
                        "bound_client_id": "ext_7HUtMOXF5Qq0",
                    }
                ]

        app = FastAPI()
        app.state.telemetry = telemetry
        app.state.registry = SimpleNamespace(
            clients={"ext_7HUtMOXF5Qq0": SimpleNamespace(pairing_id="pair_N9aNI5vAn4")}
        )
        app.state.pairings = Pairings()
        install_request_device_identity_patch(app)

        row = telemetry.query(limit=10)["data"][0]
        assert row["device_name"] == "ubuntu03"
        assert row["device_code_id"] == "pair_N9aNI5vAn4"
        assert row["worker_client_id"] == "ext_7HUtMOXF5Qq0"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "filename",
    [
        "chrome_extension/background_transport_recovery_v47.js",
        "app/admin_request_device_identity_v47.js",
        "app/admin_request_history_v93.js",
    ],
)
def test_new_worker_assets_parse(filename):
    result = subprocess.run(
        ["node", "--check", str(ROOT / filename)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_background_loads_transport_outbox_after_request_recovery():
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index('"background_request_recovery_v40.js"') < entry.index('"background_transport_recovery_v47.js"')


def test_admin_assets_use_canonical_device_and_worker_terms_with_single_row_owner():
    terminology = (ROOT / "app" / "admin_request_device_identity_v47.js").read_text(encoding="utf-8")
    requests = (ROOT / "app" / "admin_request_history_v93.js").read_text(encoding="utf-8")
    assert '[/配对码/g, "设备码"]' in terminology
    assert '[/扩展/g, "Worker"]' in terminology
    assert "rqBody" not in terminology
    assert '<th data-chat2api-device-identity="1">设备标识</th>' in requests
    assert "worker_client_id" in requests
    assert "device_code_id" in requests


def test_entry_installs_transport_recovery_after_request_recovery_and_identity_last():
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert entry.index("install_request_recovery_patch(app)") < entry.index("install_worker_transport_recovery_patch(app)")
    assert entry.index("install_linux_worker_console_polling_patch(app)") < entry.index("install_request_device_identity_patch(app)")
