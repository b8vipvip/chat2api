import asyncio
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.linux_worker_bridge_binding import install_linux_worker_bridge_binding_patch
from app.linux_worker_pairing_patch import install_linux_worker_pairing_patch
from app.linux_workers import LinuxWorkerStore
from app.pairing import PairingStore
from app.registry import ClientRegistry


ROOT = Path(__file__).resolve().parents[1]


class _Settings:
    def __init__(self, path: Path):
        self.data_dir = path

    def resolved_public_url(self, _: str) -> str:
        return "https://chat2api.example"


class _AdminSessions:
    def authenticate(self, _token):
        return True


def _app_for(path: Path):
    app = FastAPI()
    app.state.settings = _Settings(path)
    app.state.admin_sessions = _AdminSessions()
    app.state.linux_workers = LinuxWorkerStore(path)
    app.state.registry = ClientRegistry(path)
    app.state.pairings = PairingStore(path)
    install_linux_worker_bridge_binding_patch(app)
    install_linux_worker_pairing_patch(app)
    return app


def test_agent_extension_enrollment_is_not_blocked_by_chatgpt_proxy_probe():
    source = (ROOT / "scripts" / "linux_worker_agent.py").read_text(encoding="utf-8")
    binding_loop = source.split("async def _binding_loop", 1)[1].split("async def main", 1)[0]

    assert 'AGENT_VERSION = "0.3.4"' in source
    assert "_request_binding_ticket" in binding_loop
    assert "inject_worker_binding" in binding_loop
    assert "service_active(\"chat2api-chrome.service\")" in binding_loop
    assert "proxy_configured()" not in binding_loop
    assert "_proxy_test" not in binding_loop
    assert 'report("ticket_unavailable")' in binding_loop
    assert 'report("inject_failed"' in binding_loop
    assert "ticket" not in source.split('print(f"[linux-worker] extension-binding', 1)[1].split("flush=True", 1)[0]


def test_fresh_worker_claims_extension_then_login_event_auto_binds_saved_pairing(tmp_path):
    app = _app_for(tmp_path)
    workers = app.state.linux_workers
    registry = app.state.registry
    pairings = app.state.pairings

    enrollment = workers.create_enrollment("Fresh Worker")
    credentials = workers.enroll(
        enrollment["code"],
        {"hostname": "fresh-worker", "platform": "linux", "agent_version": "0.3.4"},
    )
    worker_id = credentials["worker_id"]
    pairing, raw_pairing_code = asyncio.run(pairings.create("Fresh Worker Pairing"))

    @app.post("/_test/bridge-ready/{target_client_id}")
    async def bridge_ready(target_client_id: str):
        await registry.touch(
            target_client_id,
            {
                "chatgpt_login_state": "ready",
                "chatgpt_login_composer_ready": True,
                "chatgpt_login_confidence": "high",
                "network_probe_status": "external",
                "network_country_code": "US",
            },
        )
        return {"ok": True}

    headers = {
        "X-Worker-ID": worker_id,
        "X-Worker-Token": credentials["worker_token"],
    }

    with TestClient(app) as client:
        saved = client.put(
            f"/api/admin/linux-workers/{worker_id}/pairing-code",
            json={"pairing_code": raw_pairing_code},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["reconcile"]["status"] == "pending"
        assert workers.data["workers"][worker_id]["extension_client_id"] == ""

        issued = client.post("/api/workers/extension-binding-ticket", headers=headers)
        assert issued.status_code == 200, issued.text
        ticket = issued.json()["ticket"]

        claimed = client.post(
            "/api/extensions/worker-bind",
            json={
                "ticket": ticket,
                "device_id": "fresh-device-0001",
                "name": "Linux Worker Bridge",
                "browser_name": "Chrome",
                "version": "0.8.1",
                "metadata": {"runtime_id": "runtime-fresh"},
            },
        )
        assert claimed.status_code == 200, claimed.text
        client_id = claimed.json()["client_id"]
        assert workers.data["workers"][worker_id]["extension_client_id"] == client_id

        ready = client.post(f"/_test/bridge-ready/{client_id}")
        assert ready.status_code == 200, ready.text

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if pairings.get(pairing["pairing_id"]).bound_client_id == client_id:
                break
            time.sleep(0.02)

        worker = workers.data["workers"][worker_id]
        bridge = worker["metadata"]["bridge"]
        pairing_state = worker["metadata"]["worker_pairing"]

        assert bridge["chatgpt_logged_in"] is True
        assert bridge["extension_id"] == client_id
        assert bridge["pairing_id"] == pairing["pairing_id"]
        assert pairing_state["status"] == "bound"
        assert pairing_state["bound_client_id"] == client_id
        assert pairings.get(pairing["pairing_id"]).bound_client_id == client_id
        assert registry.clients[client_id].pairing_id == pairing["pairing_id"]
