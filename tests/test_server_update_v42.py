from __future__ import annotations

import json
import subprocess
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.server_update_patch as update_patch
from app.server_update_patch import install_server_update_patch


ROOT = Path(__file__).resolve().parents[1]


class Settings:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir


class Sessions:
    def authenticate(self, token: str | None) -> bool:
        return token == "admin-ok"


def app_for(path: Path) -> FastAPI:
    app = FastAPI()
    app.state.settings = Settings(path)
    app.state.admin_sessions = Sessions()
    install_server_update_patch(app)
    return app


def test_update_api_is_session_protected_and_only_queues_fixed_request(tmp_path, monkeypatch):
    async def fake_remote(_app, *, refresh=False):
        return {
            "ok": True,
            "sha": "b" * 40,
            "short_sha": "b" * 12,
            "message": "remote main",
            "url": "https://github.com/b8vipvip/chat2api/commit/" + "b" * 40,
        }

    monkeypatch.setattr(update_patch, "_remote_main", fake_remote)
    (tmp_path / update_patch.UPDATER_MARKER_NAME).write_text(
        json.dumps({"installed": True, "mode": "systemd-path", "app_dir": "/opt/chat2api"}),
        encoding="utf-8",
    )
    (tmp_path / update_patch.DEPLOYMENT_NAME).write_text(
        json.dumps({"commit": "a" * 40, "branch": "main"}), encoding="utf-8"
    )
    app = app_for(tmp_path)

    with TestClient(app) as client:
        denied = client.get("/api/admin/server-update/status")
        assert denied.status_code == 401

        client.cookies.set("chat2api_admin_session", "admin-ok")
        overview = client.get("/api/admin/server-update?refresh=1")
        assert overview.status_code == 200
        payload = overview.json()
        assert payload["repository"] == "b8vipvip/chat2api"
        assert payload["branch"] == "main"
        assert payload["update_available"] is True
        assert payload["updater"]["installed"] is True
        assert payload["updater"]["install_command"].endswith("install_chat2api_server_updater.sh")

        queued = client.post("/api/admin/server-update/start", json={"confirm": True})
        assert queued.status_code == 202
        request = json.loads((tmp_path / update_patch.UPDATE_REQUEST_NAME).read_text(encoding="utf-8"))
        assert request["repository"] == "b8vipvip/chat2api"
        assert request["branch"] == "main"
        assert request["request_id"].startswith("upd_")
        assert "command" not in request
        assert "shell" not in request

        status = client.get("/api/admin/server-update/status").json()
        assert status["status"] == "queued"
        assert status["updater_installed"] is True

        duplicate = client.post("/api/admin/server-update/start", json={"confirm": True})
        assert duplicate.status_code == 409


def test_update_console_and_host_scripts_use_narrow_systemd_boundary():
    ui = (ROOT / "app" / "admin_server_update.js").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "server_update_patch.py").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_chat2api_server_updater.sh").read_text(encoding="utf-8")
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    for token in (
        "版本更新",
        "从 GitHub 更新服务端",
        "systemd.path + 固定更新脚本",
        "install_chat2api_server_updater.sh",
        "/api/admin/server-update/start",
    ):
        assert token in ui
    assert 'GITHUB_REPOSITORY = "b8vipvip/chat2api"' in patch
    assert 'UPDATE_REQUEST_NAME = "admin-update-request.json"' in patch
    assert 'PathExists=${DATA_DIR}/admin-update-request.json' in installer
    assert "chat2api-admin-update.service" in installer
    assert "git -C \"$APP_DIR\" config http.version HTTP/1.1" in updater
    assert "fetch_main" in updater
    assert "DOCKER_BUILDKIT=1 docker compose build" in updater
    assert "rollback()" in updater
    assert "wait_health" in updater
    assert "/var/run/docker.sock" not in compose
    assert "install_server_update_patch(app)" in entry
    assert entry.rfind("install_server_update_patch(app)") > entry.rfind("install_linux_worker_diagnostics_patch(app)")


def test_update_scripts_and_ui_parse():
    for filename in (
        "scripts/install_chat2api_server_updater.sh",
        "scripts/chat2api_server_update.sh",
    ):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / filename)], capture_output=True, text=True, check=False
        )
        assert result.returncode == 0, result.stderr

    js = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_server_update.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert js.returncode == 0, js.stderr
