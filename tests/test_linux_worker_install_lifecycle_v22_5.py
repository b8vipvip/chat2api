from pathlib import Path

from app.linux_worker_installs import LinuxWorkerInstallStore


ROOT = Path(__file__).resolve().parents[1]


def test_install_record_has_no_time_expiry_and_tracks_terminal_disable(tmp_path):
    store = LinuxWorkerInstallStore(tmp_path)
    item = store.create("US Worker")
    assert item["state"] == "pending"
    assert item["enabled"] is True
    assert "expires_at" not in item
    assert item["code"]

    installing = store.record_progress(item["code"], stage="packages", state="installing", message="安装依赖")
    assert installing["state"] == "installing"
    assert installing["stage"] == "packages"
    assert installing["enabled"] is True

    failed = store.record_progress(item["code"], stage="worker-bundle", state="failed", message="下载失败")
    assert failed["state"] == "failed"
    assert failed["enabled"] is False
    assert failed["failed_at"]


def test_pending_disabled_command_can_be_reenabled_but_consumed_cannot(tmp_path):
    store = LinuxWorkerInstallStore(tmp_path)
    item = store.create("Worker")
    disabled = store.update(item["install_id"], enabled=False)
    assert disabled["state"] == "disabled"
    enabled = store.update(item["install_id"], enabled=True)
    assert enabled["state"] == "pending"
    linked = store.link_worker(item["code"], "wrk_test")
    assert linked["worker_id"] == "wrk_test"
    try:
        store.update(item["install_id"], enabled=True)
    except ValueError as exc:
        assert "consumed" in str(exc).lower()
    else:
        raise AssertionError("consumed install command must not be re-enabled")


def test_bootstrap_uses_center_bundle_domestic_pip_and_isolated_worker_dir():
    source = (ROOT / "scripts" / "bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert 'WORKER_DIR="/opt/chat2api-worker"' in source
    assert "/bootstrap/linux-worker-bundle.json" in source
    assert "/bootstrap/linux-worker-bundle.tar.gz" in source
    assert "/bootstrap/xray/latest.json" in source
    assert "/bootstrap/xray/latest.zip" in source
    assert "https://pypi.tuna.tsinghua.edu.cn/simple" in source
    assert "git clone" not in source
    assert "github.com/b8vipvip/chat2api" not in source
    assert 'set_stage "cleanup"' in source
    assert "rm -rf /opt/chat2api-worker-venv" in source
    assert "PROFILE_DIR" in source
    assert "/api/workers/install-progress" in source


def test_server_packages_bundle_and_admin_uses_worker_lifecycle_list():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    api = (ROOT / "app" / "linux_worker_install_patch.py").read_text(encoding="utf-8")
    admin = (ROOT / "app" / "admin_linux_workers.js").read_text(encoding="utf-8")
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")

    assert "COPY chrome_extension ./worker_payload/chrome_extension" in dockerfile
    assert "linux-worker-bundle.tar.gz" in dockerfile
    assert '@app.post("/api/workers/install-progress")' in api
    assert '@app.get("/bootstrap/linux-worker-bundle.tar.gz"' in api
    assert 'ACTIVE_EXPIRES_AT = "9999-12-31T23:59:59Z"' in api
    assert "/api/admin/linux-worker-installations" in admin
    assert "安装完成或失败后自动停用" in admin
    assert "有效期至" not in admin
    assert 'SERVER_RUNTIME_VERSION = "0.22.6"' in runtime
    assert 'CHROME_BRIDGE_VERSION = "0.8.1"' in runtime