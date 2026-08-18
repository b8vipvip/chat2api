import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.linux_worker_patch import install_linux_worker_patch
from app.linux_workers import ALLOWED_COMMANDS, LinuxWorkerStore, token_hash


class Settings:
    def __init__(self, path: Path):
        self.data_dir = path

    def resolved_public_url(self, _: str) -> str:
        return "https://chat2api.example"


class Sessions:
    def authenticate(self, token):
        return token == "admin-ok"


def app_for(path: Path) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(path)
    app.state.admin_sessions = Sessions()
    return install_linux_worker_patch(app)


def test_enrollment_generation_one_time_auth_revoke_and_redaction(tmp_path):
    store = LinuxWorkerStore(tmp_path)
    enrollment = store.create_enrollment("US-01", 30)
    assert len(enrollment["code"].split("-")) == 3
    credentials = store.enroll(enrollment["code"], {"hostname": "worker-1", "proxy_config": "vless://secret"})
    assert store.authenticate(credentials["worker_id"], credentials["worker_token"])
    with pytest.raises(ValueError, match="already used"):
        store.enroll(enrollment["code"], {})
    public = store.list_public()[0]
    assert "token_hash" not in public
    assert "proxy_config" not in json.dumps(public)
    store.revoke(credentials["worker_id"])
    assert not store.authenticate(credentials["worker_id"], credentials["worker_token"])


def test_expired_and_invalid_enrollment(tmp_path):
    store = LinuxWorkerStore(tmp_path)
    enrollment = store.create_enrollment("expired")
    stored = store.data["enrollments"][token_hash(enrollment["code"])]
    stored["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(ValueError, match="expired"):
        store.enroll(enrollment["code"], {})
    with pytest.raises(ValueError, match="Invalid"):
        store.enroll("NOPE-NOPE-NOPE", {})


def test_admin_list_enroll_worker_status_and_unauthorized(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        assert client.get("/api/admin/linux-workers").status_code == 401
        client.cookies.set("chat2api_admin_session", "admin-ok")
        created = client.post("/api/admin/linux-workers/enrollments", json={"name": "JP-01", "ttl_minutes": 10})
        assert created.status_code == 200
        assert "worker_token" not in created.text
        enrolled = client.post("/api/workers/enroll", json={"enroll_code": created.json()["code"], "hostname": "jp-1", "platform": "linux", "arch": "x86_64"})
        assert enrolled.status_code == 200
        credentials = enrolled.json()
        assert credentials["websocket_url"].startswith("wss://")
        workers = client.get("/api/admin/linux-workers").json()["data"]
        assert workers[0]["status"] == "enrolling"
        assert "worker_token" not in json.dumps(workers)
        rejected = client.post(f"/api/admin/linux-workers/{credentials['worker_id']}/commands", json={"command": "exec_shell"})
        assert rejected.status_code == 400
        assert client.delete(f"/api/admin/linux-workers/{credentials['worker_id']}").json() == {"revoked": True}


def test_command_allowlist_has_no_arbitrary_execution():
    required = {"health_check", "restart_chrome", "restart_xray", "restart_xvfb", "reload_extension", "test_proxy", "apply_proxy_config", "open_login_session", "close_login_session", "get_logs", "reconcile_reserve_pool"}
    assert ALLOWED_COMMANDS == required
    assert not {"exec_shell", "run_command", "bash"} & ALLOWED_COMMANDS


def test_bootstrap_is_strict_idempotent_and_preserves_profile():
    source = Path("scripts/bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert "set -euo pipefail" in source
    assert 'if [[ ! -s /etc/chat2api-worker/worker.json ]]' in source
    assert 'if [[ ! -s /etc/chat2api-worker/xray.json ]]' in source
    assert "user-data-dir=/home/chat2api/.config/chat2api-chrome-worker-01" in source
    assert "rm -rf /home/chat2api" not in source
    assert "NOPASSWD: ALL" not in source
    assert "--no-sandbox" not in source


def test_bootstrap_config_permissions_allow_unprivileged_services_to_read():
    source = Path("scripts/bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert "install -d -o root -g chat2api -m 750 /etc/chat2api-worker" in source
    assert "chown root:chat2api /etc/chat2api-worker/xray.json" in source
    assert "chmod 640 /etc/chat2api-worker/xray.json" in source
    assert "chown root:chat2api /etc/chat2api-worker/worker.json" in source
    assert "chmod 640 /etc/chat2api-worker/worker.json" in source
    assert "User=chat2api" in source


def test_worker_agent_uses_only_noninteractive_restricted_sudo_and_reports_unimplemented_commands():
    agent = Path("scripts/linux_worker_agent.py").read_text(encoding="utf-8")
    bootstrap = Path("scripts/bootstrap_linux_worker.sh").read_text(encoding="utf-8")
    assert '["sudo", "-n", "/bin/systemctl", "restart"' in agent
    assert 'return {"ok": False, "error": "not_implemented"}' in agent
    assert 'status = "waiting_proxy"' in agent
    assert "NOPASSWD: /bin/systemctl restart chat2api-chrome.service" in bootstrap
    agent_unit = bootstrap.split("cat >/etc/systemd/system/chat2api-worker-agent.service", 1)[1].split("\nUNIT\n", 1)[0]
    assert "NoNewPrivileges=true" not in agent_unit
