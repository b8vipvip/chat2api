import asyncio
from pathlib import Path
import subprocess

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


def test_worker_pairing_control_plane_stores_reference_not_raw_secret_and_reconciles_on_login():
    source = (ROOT / "app" / "linux_worker_pairing_patch.py").read_text(encoding="utf-8")

    for token in (
        '/api/admin/linux-workers/{worker_id}/pairing-code',
        'hashlib.sha256(raw_code.encode("utf-8")).hexdigest()',
        '"worker_pairing"',
        '"pairing_id": pairing.pairing_id',
        '"name": pairing.name',
        '"prefix": pairing.prefix',
        'str(worker.get("chatgpt_status") or "").lower() != "ready"',
        'await pairings.bind(pairing_id, client_id, device_id)',
        'client.pairing_id = pairing_id',
        'workers.record_extension_status = record_extension_status_with_pairing',
        'path == "/api/workers/extension-binding-ticket"',
    ):
        assert token in source

    # The pasted secret is only used for validation. It must never be copied into
    # the persisted Worker metadata payload.
    state_block = source.split("def write_pairing_state", 1)[1].split("async def unbind_previous", 1)[0]
    assert "raw_code" not in state_block
    assert 'metadata["worker_pairing"] = current' in state_block


def test_worker_ui_uses_requested_chinese_columns_beijing_time_and_real_status_fields():
    source = (ROOT / "app" / "admin_linux_worker_pairing.js").read_text(encoding="utf-8")

    assert '["名称","状态","安装进度","安装命令","系统","网络","代理","ChatGPT","最后更新","操作"]' in source
    assert '"Chrome Bridge"' not in source
    assert '"备用窗口"' not in source
    assert 'timeZone:"Asia/Shanghai"' in source
    assert 'row.last_seen_at || row.install_updated_at' in source
    assert 'return logged ? "已登录" : "未登录"' in source
    assert 'return `已连接（${name}）`' in source
    assert 'button.textContent = "配对码"' in source
    assert 'metadata.proxy_summary' in source
    assert 'meta.worker_pairing' in source
    assert '/api/admin/linux-worker-installations' in source
    assert '/api/admin/linux-worker-proxies' in source


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


def test_pairing_ui_javascript_has_valid_syntax():
    result = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_linux_worker_pairing.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_runtime_marks_worker_pairing_ui_release_without_bridge_protocol_bump():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.18"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime
    assert "install_runtime_contract(app)" in entry
    assert "install_linux_worker_pairing_patch(app)" in entry
    assert "install_linux_worker_proxy_name_patch(app)" in entry
    assert entry.index("install_runtime_contract(app)") < entry.index("install_linux_worker_pairing_patch(app)")
    assert entry.index("install_linux_worker_pairing_patch(app)") < entry.index("install_linux_worker_proxy_name_patch(app)")
