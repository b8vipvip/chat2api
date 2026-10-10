"""Cross-platform Worker console and non-navigating remote desktop contracts."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.linux_worker_login_freshness_patch import install_linux_worker_login_freshness_patch
from app.linux_worker_login_sessions import LoginSessionStore
from app.admin_auth import SESSION_COOKIE

ROOT = Path(__file__).resolve().parents[1]
OPEN_PATH = "/api/admin/linux-workers/{worker_id}/login-session"
FRAME_PATH = "/api/admin/linux-workers/{worker_id}/login-session/frame"


def load_remote_helper():
    path = ROOT / "scripts/linux_worker_remote_login.py"
    module_name = "chat2api_test_remote_view_v155"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_remote_view_does_not_open_login_page_or_change_focus(monkeypatch):
    helper = load_remote_helper()
    helper.SESSION.close()

    def forbidden(*args, **kwargs):
        raise AssertionError("Remote view must not navigate or focus Chrome")

    monkeypatch.setattr(helper, "_navigate_login_page", forbidden)
    monkeypatch.setattr(helper, "_open_url_via_cdp", forbidden)
    monkeypatch.setattr(helper, "_chrome_window_id", forbidden)
    monkeypatch.setattr(helper, "_focus_window", forbidden)
    result = helper.open_remote_session()
    assert result["ok"] is True
    assert result["mode"] == "remote"
    assert helper.session_active() is True
    helper.close_session()
    assert helper.session_active() is False


def test_remote_frame_stays_open_even_when_chatgpt_is_already_ready():
    app = FastAPI()
    store = LoginSessionStore()
    calls = []

    @app.post(OPEN_PATH)
    async def old_open(worker_id: str, request: Request):
        data = await request.json()
        assert data == {"mode": "remote"}
        ticket = store.issue(worker_id)
        store.require(worker_id, ticket, touch=False).mode = "remote"
        return {"ticket": ticket, "mode": "remote"}

    @app.get(FRAME_PATH)
    async def old_frame(worker_id: str):
        return {"worker_id": worker_id}

    app.state.linux_worker_control_plane_installed = True
    app.state.worker_login_sessions = store
    app.state.admin_sessions = SimpleNamespace(authenticate=lambda _cookie: True)
    app.state.linux_workers = SimpleNamespace(data={"workers": {"wrk_remote": {
        "chatgpt_status": "ready",
        "metadata": {"bridge": {"login_checked_at_ms": 3000, "login_state": "ready", "composer_ready": True}},
    }}})

    async def fake_command(worker_id, command, args, **kwargs):
        calls.append((worker_id, command))
        assert command == "login_session_frame"
        return {"result": {"ok": True, "frame": "aGVsbG8=", "mime": "image/jpeg",
                           "source_width": 1920, "source_height": 1080}}

    app.state.send_linux_worker_command = fake_command
    install_linux_worker_login_freshness_patch(app)
    client = TestClient(app)
    opened = client.post("/api/admin/linux-workers/wrk_remote/login-session", json={"mode": "remote"})
    assert opened.status_code == 200
    ticket = opened.json()["ticket"]
    headers = {"X-Chat2API-Login-Ticket": ticket}
    for _ in range(2):
        frame = client.get("/api/admin/linux-workers/wrk_remote/login-session/frame", headers=headers)
        assert frame.status_code == 200
        assert frame.json()["complete"] is False
        assert frame.json()["frame"] == "aGVsbG8="
        assert store.has_worker_session("wrk_remote")
    assert calls == [("wrk_remote", "login_session_frame")] * 2


def test_remote_mode_is_allowlisted_and_preserves_login_flow():
    worker_store = (ROOT / "app/linux_workers.py").read_text()
    server = (ROOT / "app/linux_worker_patch.py").read_text()
    controller = (ROOT / "scripts/linux_worker_device_controller.py").read_text()
    agent = (ROOT / "scripts/linux_worker_agent.py").read_text()
    freshness = (ROOT / "app/linux_worker_login_freshness_patch.py").read_text()
    ui = (ROOT / "app/admin_linux_device_authority_v124.js").read_text()
    assert '"open_remote_session"' in worker_store
    assert 'mode == "remote"' in server
    assert 'mode not in {"login", "remote"}' in server
    assert '"open_remote_session", {}, wait=True' in server
    assert '"open_login_session"' in server
    assert 'mode = "remote"' in server
    assert 'session.mode != "remote"' in freshness
    assert 'remote.open_remote_session()' in controller
    assert 'return open_remote_session()' in agent
    assert 'body:{mode:"remote"}' in ui
    assert '远程</button>' in ui
    assert 'data-login-worker' not in ui.split('function renderManager()',1)[1].split('function renamePairingHeader',1)[0]


def test_device_tables_and_worker_tables_have_separate_owners():
    windows = (ROOT / "app/admin_unified_workers_v153.js").read_text()
    linux = (ROOT / "app/admin_linux_device_authority_v124.js").read_text()
    assert 'id="windowsDeviceTableV155"' in windows
    assert 'const clients=(Array.isArray(data.clients)' in windows
    assert 'c.metadata?.linux_worker_id' in windows
    assert 'id="linuxWorkerTableV155"' in linux
    assert 'const clientById=new Map(state.extensions' in linux
    assert 'data-worker-action="initialize"' in linux.split('function renderWorkers()',1)[1]
    assert 'data-v121-worker-limits' in linux
    assert 'data-v121-save-limits' in linux
    header = linux.split('id="linuxDeviceTableV124"',1)[1].split('</thead>',1)[0]
    assert '<th>Worker ID</th>' not in header
    assert '<th>ChatGPT</th>' not in header


def test_console_javascript_syntax():
    for name in ["admin_linux_device_authority_v124.js", "admin_unified_workers_v153.js"]:
        result = subprocess.run(["node", "--check", str(ROOT / "app" / name)],
                                capture_output=True, text=True, check=False, timeout=15)
        assert result.returncode == 0, result.stderr
