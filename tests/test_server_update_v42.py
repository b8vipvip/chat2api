from __future__ import annotations

import json
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.server_update_patch as update_patch
from app.server_update_patch import install_server_update_patch
from app.telemetry import current_deployment_start


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

        queued = client.post(
            "/api/admin/server-update/start",
            json={"confirm": True, "use_build_cache": False},
        )
        assert queued.status_code == 202
        assert queued.json()["use_build_cache"] is False
        request = json.loads((tmp_path / update_patch.UPDATE_REQUEST_NAME).read_text(encoding="utf-8"))
        assert request["repository"] == "b8vipvip/chat2api"
        assert request["branch"] == "main"
        assert request["request_id"].startswith("upd_")
        assert request["use_build_cache"] is False
        assert stat.S_IMODE((tmp_path / update_patch.UPDATE_STATUS_NAME).stat().st_mode) == 0o600
        assert stat.S_IMODE((tmp_path / update_patch.UPDATE_REQUEST_NAME).stat().st_mode) == 0o600
        assert "command" not in request
        assert "shell" not in request

        status = client.get("/api/admin/server-update/status").json()
        assert status["status"] == "queued"
        assert status["updater_installed"] is True

        duplicate = client.post("/api/admin/server-update/start", json={"confirm": True})
        assert duplicate.status_code == 409


def _pkt(payload: bytes) -> bytes:
    return f"{len(payload) + 4:04x}".encode("ascii") + payload


def test_git_smart_http_parser_extracts_main_without_rest_api_quota():
    head = b"a" * 40
    main = b"b" * 40
    advertised = (
        _pkt(b"# service=git-upload-pack\n")
        + b"0000"
        + _pkt(head + b" HEAD\x00multi_ack thin-pack side-band\n")
        + _pkt(main + b" refs/heads/main\n")
        + b"0000"
    )
    assert update_patch._git_smart_main_sha(advertised) == "b" * 40


def test_update_console_and_host_scripts_use_narrow_systemd_boundary():
    ui = (ROOT / "app" / "admin_server_update.js").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "server_update_patch.py").read_text(encoding="utf-8")
    installer = (ROOT / "scripts" / "install_chat2api_server_updater.sh").read_text(encoding="utf-8")
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")

    for token in (
        "版本更新",
        "检查并智能更新",
        "systemd.path + 固定更新脚本",
        "install_chat2api_server_updater.sh",
        "/api/admin/server-update/start",
        "使用 Docker 构建缓存（推荐）",
        "use_build_cache",
        "terminalTransition",
        "previousState !== data.status",
    ):
        assert token in ui
    assert "confirm(" not in ui
    assert 'body: {confirm: true, use_build_cache: useBuildCache}' in ui
    assert 'GITHUB_REPOSITORY = "b8vipvip/chat2api"' in patch
    assert "GITHUB_SMART_REFS" in patch
    assert '"source": "git-smart-http"' in patch
    assert 'os.getenv("CHAT2API_GITHUB_TOKEN")' in patch
    assert 'UPDATE_REQUEST_NAME = "admin-update-request.json"' in patch
    assert 'PathExists=${DATA_DIR}/admin-update-request.json' in installer
    assert "chat2api-admin-update.service" in installer
    assert 'chmod 0644 "$UPDATER_SCRIPT"' in installer
    assert 'chmod 755 "$UPDATER_SCRIPT"' not in installer
    assert 'ensure-dir "$DATA_DIR"' in installer
    assert "write_json_atomic" in installer
    assert "git -C \"$APP_DIR\" config http.version HTTP/1.1" in updater
    assert "fetch_main" in updater
    assert "normalize_legacy_updater_mode" in updater
    assert "read_use_build_cache" in updater
    assert 'LOCK_FILE="${DATA_DIR}/.admin-update.lock"' in updater
    assert 'exec 9<>"$LOCK_FILE"' in updater
    assert "chat2api_update_storage.py" in updater
    assert 'ensure-dir "$DATA_DIR"' in updater
    assert 'truncate-log "$LOG_FILE"' in updater
    assert 'tee-log "$LOG_FILE"' in updater
    assert updater.index('exec 9<>"$LOCK_FILE"') < updater.index('truncate-log "$LOG_FILE"')
    assert 'if ! REQUEST_ID="$(read_request_id)" || ! USE_BUILD_CACHE="$(read_use_build_cache)"; then' in updater
    assert 'read-request-id "$REQUEST_FILE"' in updater
    assert 'read-build-cache "$REQUEST_FILE"' in updater
    assert 'copy-file "$APP_DIR/docker-compose.yml"' in updater
    assert 'copy-file "$LOCAL_COMPOSE_MERGED_FILE"' in updater
    assert 'mktemp /tmp/chat2api-update-version.XXXXXX' in updater
    assert 'mktemp -d /tmp/chat2api-env.XXXXXX' in updater
    assert 'mktemp /run/chat2api-github-known-hosts.XXXXXX' in updater
    assert 'LOCAL_COMPOSE_BACKUP_CREATED="true"' in updater
    assert 'REQUEST_CONSUMED="true"' in updater
    assert 'trap - EXIT' in updater
    assert ': > "$LOG_FILE"' not in updater
    assert 'tee -a "$LOG_FILE"' not in updater
    assert "path.read_text(" not in updater
    assert "temp.write_text(" not in updater
    assert "DOCKER_BUILDKIT=1 docker compose build" in updater
    assert "docker compose build --no-cache" in updater
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

    py = subprocess.run(
        [
            sys.executable, "-m", "py_compile",
            str(ROOT / "scripts" / "chat2api_update_storage.py"),
            str(ROOT / "scripts" / "chat2api_update_reconcile.py"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert py.returncode == 0, py.stderr

    js = subprocess.run(
        ["node", "--check", str(ROOT / "app" / "admin_server_update.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert js.returncode == 0, js.stderr

def test_update_preserves_only_docker_compose_local_override_and_recovers_it_after_forced_exit():
    updater = (ROOT / "scripts" / "chat2api_server_update.sh").read_text(encoding="utf-8")
    reconcile = (ROOT / "scripts" / "chat2api_update_reconcile.py").read_text(encoding="utf-8")
    assert "LOCAL_COMPOSE_BACKUP_FILE=" in updater
    assert ".admin-update-docker-compose.backup" in updater
    assert "prepare_local_compose_override" in updater
    assert 'git -C "$APP_DIR" merge-file -p -q' in updater
    assert "reapplied merged local docker-compose.yml" in updater
    assert 'if [[ "$path" != "docker-compose.yml" ]]' in updater
    assert 'shutil.copy2(compose_backup, compose_target)' in reconcile


def test_update_state_and_log_helpers_reject_symlinked_files(tmp_path):
    status_target = tmp_path / "outside-update-status"
    status_target.write_text('{"status":"running"}', encoding="utf-8")
    status = tmp_path / update_patch.UPDATE_STATUS_NAME
    status.symlink_to(status_target)
    assert update_patch._read_json(status) == {}
    with pytest.raises(RuntimeError, match="symbolic link"):
        update_patch._write_json_atomic(status, {"status": "queued"})
    assert status_target.read_text(encoding="utf-8") == '{"status":"running"}'

    log_target = tmp_path / "outside-update-log"
    log_target.write_text("sensitive external log line\\n", encoding="utf-8")
    log = tmp_path / update_patch.UPDATE_LOG_NAME
    log.symlink_to(log_target)
    assert update_patch._tail_log(log) == []
    assert log_target.read_text(encoding="utf-8") == "sensitive external log line\\n"


def test_concurrent_api_workers_cannot_queue_two_server_updates(tmp_path, monkeypatch):
    (tmp_path / update_patch.UPDATER_MARKER_NAME).write_text(
        json.dumps({"installed": True, "mode": "systemd-path"}),
        encoding="utf-8",
    )
    app = app_for(tmp_path)
    original_status = update_patch._status_payload
    counter_lock = threading.Lock()
    active = 0
    peak_active = 0

    def slow_status(app_state):
        nonlocal active, peak_active
        with counter_lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            time.sleep(0.08)
            return original_status(app_state)
        finally:
            with counter_lock:
                active -= 1

    monkeypatch.setattr(update_patch, "_status_payload", slow_status)

    def start_update():
        with TestClient(app) as client:
            client.cookies.set("chat2api_admin_session", "admin-ok")
            return client.post(
                "/api/admin/server-update/start",
                json={"confirm": True},
            ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: start_update(), range(2)))

    assert sorted(statuses) == [202, 409]
    assert peak_active == 1, "the status check and queue write must share one cross-worker lock"
    request = json.loads((tmp_path / update_patch.UPDATE_REQUEST_NAME).read_text(encoding="utf-8"))
    assert request["request_id"].startswith("upd_")
    assert stat.S_IMODE((tmp_path / update_patch.UPDATE_STATUS_NAME).stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / update_patch.UPDATE_REQUEST_NAME).stat().st_mode) == 0o600


def test_telemetry_deployment_filter_does_not_follow_update_status_symlink(tmp_path):
    target = tmp_path / "outside-deployment-status"
    target.write_text(json.dumps({
        "status": "succeeded",
        "deployed_commit": "b" * 40,
        "completed_at": "2026-11-22T00:00:00+00:00",
    }), encoding="utf-8")
    (tmp_path / update_patch.UPDATE_STATUS_NAME).symlink_to(target)
    assert current_deployment_start(tmp_path, "b" * 40) is None
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "succeeded"
