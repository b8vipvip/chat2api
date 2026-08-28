import asyncio
from pathlib import Path
import subprocess
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.linux_worker_pairing_patch import install_linux_worker_pairing_patch
from app.linux_workers import LinuxWorkerStore
from app.pairing import PairingStore
from app.registry import ClientRegistry


ROOT = Path(__file__).resolve().parents[1]


class _AdminSessions:
    def authenticate(self, _token):
        return True


def test_bound_worker_telemetry_does_not_rebind_pairing_on_every_pulse(tmp_path):
    workers = LinuxWorkerStore(tmp_path)
    enrollment = workers.create_enrollment("Freeze Regression Worker")
    credentials = workers.enroll(enrollment["code"], {"platform": "linux"})
    worker_id = credentials["worker_id"]
    workers.record_proxy_success(worker_id, {"protocol": "vless", "server": "us03", "port": 443})

    registry = ClientRegistry(tmp_path)
    device_id = "freeze-regression-device-0001"
    client_id, _ = asyncio.run(registry.register("Bridge", "Chrome", "0.8.5", {}, device_id=device_id))
    workers.bind_extension(worker_id, client_id, device_id)

    pairings = PairingStore(tmp_path)
    pairing, raw_code = asyncio.run(pairings.create("Freeze Regression Pairing"))

    app = FastAPI()
    app.state.linux_workers = workers
    app.state.registry = registry
    app.state.pairings = pairings
    app.state.admin_sessions = _AdminSessions()
    install_linux_worker_pairing_patch(app)

    ready = {
        "client_id": client_id,
        "device_id": device_id,
        "version": "0.8.5",
        "online": True,
        "connection_enabled": True,
        "metadata": {
            "chatgpt_login_state": "ready",
            "chatgpt_login_composer_ready": True,
            "network_probe_status": "external",
            "network_country_code": "US",
        },
    }

    @app.post("/_test/ready")
    async def bridge_ready():
        workers.record_extension_status(worker_id, ready)
        return {"ok": True}

    with TestClient(app) as client:
        saved = client.put(f"/api/admin/linux-workers/{worker_id}/pairing-code", json={"pairing_code": raw_code})
        assert saved.status_code == 200, saved.text
        client.post("/_test/ready")

        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and pairings.get(pairing["pairing_id"]).bound_client_id != client_id:
            time.sleep(0.02)
        assert pairings.get(pairing["pairing_id"]).bound_client_id == client_id
        assert workers.data["workers"][worker_id]["metadata"]["worker_pairing"]["status"] == "bound"

        pairing_saves = 0
        registry_saves = 0
        worker_saves = 0
        original_pairing_save = pairings.save
        original_registry_save = registry.save
        original_worker_save = workers._save

        async def counted_pairing_save():
            nonlocal pairing_saves
            pairing_saves += 1
            return await original_pairing_save()

        async def counted_registry_save():
            nonlocal registry_saves
            registry_saves += 1
            return await original_registry_save()

        def counted_worker_save():
            nonlocal worker_saves
            worker_saves += 1
            return original_worker_save()

        pairings.save = counted_pairing_save
        registry.save = counted_registry_save
        workers._save = counted_worker_save

        for _ in range(20):
            response = client.post("/_test/ready")
            assert response.status_code == 200

        time.sleep(0.25)
        assert pairing_saves == 0
        assert registry_saves == 0
        # The authoritative bridge heartbeat still persists once per pulse; the
        # pairing layer must not add extra linux_workers.json rewrites.
        assert worker_saves == 20


def test_pairing_patch_has_single_flight_and_idempotent_bound_fast_path():
    source = (ROOT / "app" / "linux_worker_pairing_patch.py").read_text(encoding="utf-8")
    for token in (
        'PATCH_VERSION = "0.22.27"',
        'reconcile_tasks: dict[str, asyncio.Task[Any]] = {}',
        'def schedule_reconcile(worker_id: str)',
        'and client.pairing_id == pairing_id',
        '"unchanged": True',
        'schedule_reconcile(worker_id)',
        'all(current.get(key) == value for key, value in values.items())',
    ):
        assert token in source


def test_linux_worker_poll_guard_coalesces_and_times_out_list_requests():
    source = (ROOT / "app" / "admin_linux_worker_poll_guard.js").read_text(encoding="utf-8")
    for token in (
        '__CHAT2API_LINUX_WORKER_POLL_GUARD_V22_27__',
        'const TARGET = "/api/admin/linux-worker-installations"',
        'const TIMEOUT_MS = 8000',
        'if (!inflight)',
        'controller.abort("linux-worker-console-timeout")',
        'return responseFrom(await inflight)',
    ):
        assert token in source

    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_linux_worker_poll_guard.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_table_stability_patch_injects_poll_guard_before_stable_renderer():
    source = (ROOT / "app" / "linux_worker_table_stability_patch.py").read_text(encoding="utf-8")
    assert 'POLL_GUARD_ASSET = "/assets/chat2api-linux-worker-poll-guard-v22-27.js"' in source
    assert 'Path(__file__).with_name("admin_linux_worker_poll_guard.js")' in source
    assert 'poll_guard_marker = f\'<script src="{POLL_GUARD_ASSET}"></script>\'' in source
