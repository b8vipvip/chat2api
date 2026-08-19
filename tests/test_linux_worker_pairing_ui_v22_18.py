from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


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


def test_proxy_name_is_persisted_only_after_real_connected_worker_summary_exists():
    source = (ROOT / "app" / "linux_worker_pairing_patch.py").read_text(encoding="utf-8")
    block = source.split("async def save_worker_proxy_label", 1)[1].split("@app.get(PAIRING_ASSET", 1)[0]
    assert 'worker.get("proxy_status")' in block
    assert 'metadata.get("proxy_summary")' in block
    assert 'summary["name"] = name' in block
    assert 'workers._save()' in block


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
    assert "install_runtime_contract(app)\n\n# This final Worker patch" in entry
    assert "install_linux_worker_pairing_patch(app)" in entry
