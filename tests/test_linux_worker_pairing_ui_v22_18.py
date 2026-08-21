import asyncio
from pathlib import Path
import subprocess
import time

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.linux_worker_pairing_patch import install_linux_worker_pairing_patch
from app.linux_worker_proxy_catalog_patch import LinuxWorkerProxyCatalog
from app.linux_worker_proxy_name_patch import install_linux_worker_proxy_name_patch
from app.linux_workers import LinuxWorkerStore
from app.pairing import PairingStore
from app.registry import ClientRegistry


ROOT = Path(__file__).resolve().parents[1]


class _AdminSessions:
    def authenticate(self, _token):
        return True


def test_saved_pairing_code_binds_to_the_existing_logged_in_worker_extension(tmp_path):
    workers = LinuxWorkerStore(tmp_path)
    enrollment = workers.create_enrollment("US Worker")
    credentials = workers.enroll(
        enrollment["code"],
        {"hostname": "worker-1", "platform": "linux", "arch": "x86_64", "os_version": "Ubuntu 24.04"},
    )
    worker_id = credentials["worker_id"]
    workers.record_proxy_success(worker_id, {"protocol": "vless", "server": "proxy.example", "port": 443})

    registry = ClientRegistry(tmp_path)
    device_id = "device-worker-0001"
    client_id, _token = asyncio.run(
        registry.register(
            "Linux Worker Bridge",
            "Chrome",
            "0.8.1",
            {"device_id": device_id},
            device_id=device_id,
        )
    )
    workers.bind_extension(worker_id, client_id, device_id)
    workers.record_extension_status(
        worker_id,
        {
            "client_id": client_id,
            "device_id": device_id,
            "version": "0.8.1",
            "online": True,
            "connection_enabled": True,
            "metadata": {
                "chatgpt_login_state": "ready",
                "chatgpt_login_composer_ready": True,
                "network_probe_status": "external",
                "network_country_code": "US",
            },
        },
    )
    assert workers.data["workers"][worker_id]["chatgpt_status"] == "ready"

    pairings = PairingStore(tmp_path)
    pairing, raw_code = asyncio.run(pairings.create("US Worker Pairing"))

    app = FastAPI()
    app.state.linux_workers = workers
    app.state.registry = registry
    app.state.pairings = pairings
    app.state.admin_sessions = _AdminSessions()
    install_linux_worker_pairing_patch(app)

    with TestClient(app) as client:
        response = client.put(
            f"/api/admin/linux-workers/{worker_id}/pairing-code",
            json={"pairing_code": raw_code},
        )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["pairing"]["status"] == "bound"
        assert payload["pairing"]["pairing_id"] == pairing["pairing_id"]

    persisted = workers.data["workers"][worker_id]["metadata"]["worker_pairing"]
    assert persisted["pairing_id"] == pairing["pairing_id"]
    assert raw_code not in str(workers.data["workers"][worker_id])
    assert pairings.get(pairing["pairing_id"]).bound_client_id == client_id
    assert registry.clients[client_id].pairing_id == pairing["pairing_id"]


def test_bridge_login_event_automatically_reconciles_saved_pairing(tmp_path):
    workers = LinuxWorkerStore(tmp_path)
    enrollment = workers.create_enrollment("Automatic Worker")
    credentials = workers.enroll(enrollment["code"], {"platform": "linux"})
    worker_id = credentials["worker_id"]
    workers.record_proxy_success(worker_id, {"protocol": "vless", "server": "us03", "port": 443})
    registry = ClientRegistry(tmp_path)
    device_id = "automatic-device-0001"
    client_id, _ = asyncio.run(registry.register("Bridge", "Chrome", "0.8.1", {}, device_id=device_id))
    workers.bind_extension(worker_id, client_id, device_id)
    pairings = PairingStore(tmp_path)
    pairing, raw_code = asyncio.run(pairings.create("Automatic Pairing"))

    app = FastAPI()
    app.state.linux_workers = workers
    app.state.registry = registry
    app.state.pairings = pairings
    app.state.admin_sessions = _AdminSessions()
    install_linux_worker_pairing_patch(app)
    with TestClient(app) as client:
        saved = client.put(f"/api/admin/linux-workers/{worker_id}/pairing-code", json={"pairing_code": raw_code})
        assert saved.status_code == 200
        assert saved.json()["reconcile"]["status"] == "pending"

        workers.record_extension_status(worker_id, {
            "client_id": client_id, "device_id": device_id, "online": True, "connection_enabled": True,
            "metadata": {"chatgpt_login_state": "ready", "chatgpt_login_composer_ready": True},
        })
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and pairings.get(pairing["pairing_id"]).bound_client_id != client_id:
            time.sleep(0.02)

        assert pairings.get(pairing["pairing_id"]).bound_client_id == client_id
        bridge = workers.data["workers"][worker_id]["metadata"]["bridge"]
        assert bridge["chatgpt_logged_in"] is True
        assert bridge["extension_id"] == client_id
        assert workers.data["workers"][worker_id]["metadata"]["worker_pairing"]["status"] == "bound"

        reconciled = client.post(f"/api/admin/linux-workers/{worker_id}/pairing-reconcile")
        assert reconciled.status_code == 200
        assert reconciled.json()["status"] == "bound"


def test_proxy_apply_body_reaches_existing_backend_and_catalog_name_is_persisted(tmp_path):
    workers = LinuxWorkerStore(tmp_path)
    enrollment = workers.create_enrollment("US Worker")
    credentials = workers.enroll(
        enrollment["code"],
        {"hostname": "worker-1", "platform": "linux", "arch": "x86_64", "os_version": "Ubuntu 24.04"},
    )
    worker_id = credentials["worker_id"]
    share_link = "vless://11111111-1111-1111-1111-111111111111@proxy.example:443?security=tls#US-01"

    catalog = LinuxWorkerProxyCatalog(tmp_path)
    catalog.create("美国-01", share_link)

    app = FastAPI()
    app.state.linux_workers = workers
    app.state.linux_worker_proxy_catalog = catalog
    received: dict[str, str] = {}

    @app.post("/api/admin/linux-workers/{route_worker_id}/proxy")
    async def existing_proxy_backend(route_worker_id: str, request: Request):
        body = await request.json()
        received["worker_id"] = route_worker_id
        received["share_link"] = str(body.get("share_link") or "")
        workers.record_proxy_success(
            route_worker_id,
            {"protocol": "vless", "server": "proxy.example", "port": 443},
        )
        return {"ok": True, "proxy": {"protocol": "vless", "server": "proxy.example", "port": 443}}

    @app.get("/api/admin/linux-worker-installations")
    async def existing_installation_backend():
        return {"data": [workers.public(workers.data["workers"][worker_id])]}

    install_linux_worker_proxy_name_patch(app)

    with TestClient(app) as client:
        response = client.post(
            f"/api/admin/linux-workers/{worker_id}/proxy",
            json={"share_link": share_link},
        )
        assert response.status_code == 200, response.text
        assert received == {"worker_id": worker_id, "share_link": share_link}
        assert workers.data["workers"][worker_id]["metadata"]["proxy_summary"]["name"] == "美国-01"

        listing = client.get("/api/admin/linux-worker-installations")
        assert listing.status_code == 200, listing.text
        row = listing.json()["data"][0]
        assert row["proxy_status"] == "connected"
        assert row["metadata"]["proxy_summary"]["name"] == "美国-01"


def test_worker_pairing_control_plane_stores_reference_not_raw_secret_and_reconciles_on_login():
    source = (ROOT / "app" / "linux_worker_pairing_patch.py").read_text(encoding="utf-8")
    for token in (
        '/api/admin/linux-workers/{worker_id}/pairing-code',
        'hashlib.sha256(raw_code.encode("utf-8")).hexdigest()',
        '"worker_pairing"',
        'await pairings.bind(pairing_id, client_id, device_id)',
        'workers.record_extension_status = record_extension_status_with_pairing',
        'path == "/api/workers/extension-binding-ticket"',
    ):
        assert token in source

    state_block = source.split("def write_pairing_state", 1)[1].split("async def unbind_previous", 1)[0]
    assert "raw_code" not in state_block
    assert 'metadata["worker_pairing"] = current' in state_block


def test_worker_ui_is_stable_chinese_and_uses_beijing_time():
    source = (ROOT / "app" / "admin_linux_worker_stable_table.js").read_text(encoding="utf-8")
    for token in (
        'nth-child(9)',
        'nth-child(10)',
        'timeZone:"Asia/Shanghai"',
        'row.last_seen_at || row.install_updated_at',
        'return logged ? "已登录" : "未登录"',
        'return `已连接（${name}）`',
        'pairing.textContent = "配对码"',
        'remove.textContent = "删除 Worker"',
        'if (/ubuntu/i.test(os) || platform === "linux") return "Ubuntu"',
        'state === "installed" ? "完成"',
        'new MutationObserver(() => paint()).observe(tbody, {childList:true,subtree:false})',
    ):
        assert token in source


def test_stability_patch_disables_the_two_competing_v22_18_presentation_loops_and_adds_hard_delete():
    source = (ROOT / "app" / "linux_worker_table_stability_patch.py").read_text(encoding="utf-8")
    for token in (
        '__CHAT2API_LINUX_WORKER_PAIRING_UI_V22_18__=true',
        '__CHAT2API_LINUX_WORKER_CHINESE_PROGRESS_V22_18__=true',
        '/api/admin/linux-workers/{worker_id}/record',
        'workers.data["workers"].pop(worker_id, None)',
        'str(item.get("worker_id") or "") == worker_id',
    ):
        assert token in source


def test_proxy_name_is_backfilled_from_the_real_proxy_catalog_and_persisted_after_apply():
    source = (ROOT / "app" / "linux_worker_proxy_name_patch.py").read_text(encoding="utf-8")
    for token in (
        'catalog.list()',
        'str(item.get("share_link") or "").strip() == raw',
        'path == "/api/admin/linux-worker-installations"',
        'metadata.get("proxy_summary")',
        'summary["name"] = name',
        'workers._save()',
        'request.method == "POST" and path.startswith(apply_prefix) and path.endswith(apply_suffix)',
    ):
        assert token in source


def test_worker_ui_javascript_has_valid_syntax():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_linux_worker_stable_table.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_marks_worker_release_without_bridge_protocol_bump():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.22"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert "install_runtime_contract(app)" in entry
    assert "install_linux_worker_pairing_patch(app)" in entry
    assert "install_linux_worker_proxy_name_patch(app)" in entry
    assert "install_linux_worker_table_stability_patch(app)" in entry
    assert "install_linux_worker_install_ux_patch(app)" in entry
    assert "install_linux_worker_repair_command_patch(app)" in entry
    assert "install_linux_worker_diagnostics_patch(app)" in entry
    assert entry.index("install_runtime_contract(app)") < entry.index("install_linux_worker_table_stability_patch(app)")
    assert entry.index("install_linux_worker_table_stability_patch(app)") < entry.index("install_linux_worker_install_ux_patch(app)")
    assert entry.index("install_linux_worker_install_ux_patch(app)") < entry.index("install_linux_worker_repair_command_patch(app)")
    assert entry.index("install_linux_worker_repair_command_patch(app)") < entry.index("install_linux_worker_diagnostics_patch(app)")
