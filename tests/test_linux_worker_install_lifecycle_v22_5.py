from pathlib import Path
import re
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.linux_worker_install_patch import install_linux_worker_install_patch
from app.linux_worker_installs import LinuxWorkerInstallStore
from app.linux_worker_patch import install_linux_worker_patch
from app.linux_workers import token_hash


ROOT = Path(__file__).resolve().parents[1]


def _runtime_version(source: str) -> tuple[int, int, int]:
    match = re.search(r'SERVER_RUNTIME_VERSION = "(\d+)\.(\d+)\.(\d+)"', source)
    assert match
    return tuple(map(int, match.groups()))



def test_install_resume_only_accepts_the_identity_consumed_by_the_same_enrollment(tmp_path):
    app = FastAPI()
    app.state.settings = SimpleNamespace(
        data_dir=tmp_path,
        resolved_public_url=lambda _: "https://chat2api.example",
    )
    app.state.admin_sessions = SimpleNamespace(authenticate=lambda _: False)
    install_linux_worker_patch(app)
    install_linux_worker_install_patch(app)

    store = app.state.linux_workers
    install = app.state.linux_worker_installs.create("Resume Worker")
    digest = token_hash(install["code"])
    store.data["enrollments"][digest] = {
        "code_hash": digest,
        "name": install["name"],
        "created_at": install["created_at"],
        "expires_at": "9999-12-31T23:59:59Z",
        "used_at": None,
        "install_id": install["install_id"],
    }
    store._save()
    credentials = {
        "enroll_code": install["code"],
        "worker_id": "wrk_not-yet-enrolled",
        "worker_token": "not-a-token",
    }

    with TestClient(app) as client:
        # A pending enrollment cannot authorize reuse of a local identity.
        pending = client.post("/api/workers/install-resume", json=credentials)
        assert pending.status_code == 200
        assert pending.json() == {"valid": False}

        actual = store.enroll(install["code"], {"platform": "linux"})
        credentials.update(actual)

        # A used code is resumable only with the exact worker id and token.
        wrong_token = {**credentials, "worker_token": "wrong"}
        assert client.post("/api/workers/install-resume", json=wrong_token).json() == {"valid": False}
        wrong_worker = {**credentials, "worker_id": "wrk_another-worker"}
        assert client.post("/api/workers/install-resume", json=wrong_worker).json() == {"valid": False}
        wrong_code = {**credentials, "enroll_code": "AAAA-BBBB-CCCC"}
        assert client.post("/api/workers/install-resume", json=wrong_code).json() == {"valid": False}
        assert client.post("/api/workers/install-resume", json=credentials).json() == {"valid": True}
        assert app.state.linux_worker_installs.get(install["install_id"])["worker_id"] == credentials["worker_id"]
        identity_check = client.post(
            "/api/workers/identity/validate",
            json={"worker_id": credentials["worker_id"], "worker_token": credentials["worker_token"]},
        )
        assert identity_check.json() == {"valid": True, "connected": False, "heartbeat_age_seconds": None}
        app.state.worker_sockets[credentials["worker_id"]] = object()
        identity_check = client.post(
            "/api/workers/identity/validate",
            json={"worker_id": credentials["worker_id"], "worker_token": credentials["worker_token"]},
        )
        assert identity_check.json() == {"valid": True, "connected": False, "heartbeat_age_seconds": None}
        store.heartbeat(credentials["worker_id"], {"status": "ready"})
        identity_check = client.post(
            "/api/workers/identity/validate",
            json={"worker_id": credentials["worker_id"], "worker_token": credentials["worker_token"]},
        )
        assert identity_check.json()["valid"] is True
        assert identity_check.json()["connected"] is True
        assert 0 <= identity_check.json()["heartbeat_age_seconds"] <= 45
        store.data["workers"][credentials["worker_id"]]["last_seen_at"] = "2020-01-01T00:00:00Z"
        store._save()
        identity_check = client.post(
            "/api/workers/identity/validate",
            json={"worker_id": credentials["worker_id"], "worker_token": credentials["worker_token"]},
        )
        assert identity_check.json()["valid"] is True
        assert identity_check.json()["connected"] is False
        assert identity_check.json()["heartbeat_age_seconds"] > 45

        windows_enrollment = store.create_enrollment("Windows Worker")
        windows_credentials = store.enroll(windows_enrollment["code"], {"platform": "windows"})
        windows_check = client.post(
            "/api/workers/identity/validate",
            json={
                "worker_id": windows_credentials["worker_id"],
                "worker_token": windows_credentials["worker_token"],
            },
        )
        assert windows_check.json() == {"valid": False, "connected": False}


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


def test_unlinked_false_installed_record_can_be_retried(tmp_path):
    store = LinuxWorkerInstallStore(tmp_path)
    item = store.create("Unlinked Worker")
    installed = store.record_progress(
        item["code"],
        stage="complete",
        state="installed",
        message="旧安装脚本错误地报告完成",
    )
    assert installed["state"] == "installed"
    assert installed["worker_id"] is None
    assert installed["consumed_at"] is None
    assert installed["enabled"] is False

    retried = store.record_progress(
        item["code"],
        stage="cleanup",
        state="installing",
        message="重新验证并注册 Worker",
    )
    assert retried["state"] == "installing"
    assert retried["enabled"] is True
    assert retried["completed_at"] is None


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
    assert _runtime_version(runtime) >= (0, 22, 5)
    assert 'CHROME_BRIDGE_VERSION = "0.22.302"' in runtime

def test_active_install_feed_does_not_surface_orphan_legacy_workers_as_second_devices():
    source = (ROOT / "app" / "linux_worker_install_patch.py").read_text(encoding="utf-8")
    assert "Legacy/orphan Worker identities are intentionally not mixed into the" in source
    assert '"record_type": "worker"' not in source
