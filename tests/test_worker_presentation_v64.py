import asyncio
from pathlib import Path
import subprocess

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE
from app.worker_presentation_v64_patch import ADMIN_ASSET, PATCH_REVISION, install_worker_presentation_v64_patch


ROOT = Path(__file__).resolve().parents[1]


class FakePairing:
    def __init__(self, pairing_id: str, name: str, client_id: str):
        self.pairing_id = pairing_id
        self.name = name
        self.bound_client_id = client_id
        self.bound_device_id = "device-1"

    def public(self):
        return {
            "pairing_id": self.pairing_id,
            "name": self.name,
            "bound_client_id": self.bound_client_id,
            "bound_device_id": self.bound_device_id,
        }


class FakePairings:
    def __init__(self):
        item = FakePairing("pair_1", "ubuntu03", "ext_1")
        self.items = {item.pairing_id: item}
        self.lock = asyncio.Lock()
        self.saved = 0

    async def ensure_loaded(self):
        return None

    async def save(self):
        self.saved += 1


class FakeRegistry:
    def summaries(self):
        return [
            {
                "client_id": "ext_1",
                "pairing_id": "pair_1",
                "device_id": "device-1",
                "metadata": {},
                "capacity": {"used_units": 1, "limit_units": 3, "queued_requests": 0},
            }
        ]


class FakeSessions:
    def authenticate(self, token):
        return token == "admin-ok"


def build_app() -> FastAPI:
    app = FastAPI()
    app.state.registry = FakeRegistry()
    app.state.pairings = FakePairings()
    app.state.admin_sessions = FakeSessions()

    @app.get("/admin")
    async def admin():
        return HTMLResponse("<html><body>console</body></html>")

    install_worker_presentation_v64_patch(app)
    return app


def test_worker_summaries_resolve_pairing_name_dynamically():
    app = build_app()
    row = app.state.registry.summaries()[0]
    assert row["device_code_id"] == "pair_1"
    assert row["device_name"] == "ubuntu03"

    app.state.pairings.items["pair_1"].name = "US Worker 03"
    row = app.state.registry.summaries()[0]
    assert row["device_name"] == "US Worker 03"


def test_pairing_code_name_can_be_changed_from_admin_api():
    app = build_app()
    with TestClient(app) as client:
        denied = client.patch("/api/admin/pairing-codes/pair_1/name", json={"name": "renamed"})
        assert denied.status_code == 401

        client.cookies.set(SESSION_COOKIE, "admin-ok")
        response = client.patch("/api/admin/pairing-codes/pair_1/name", json={"name": "  ubuntu03-new  "})
        assert response.status_code == 200
        assert response.json()["device_name"] == "ubuntu03-new"
        assert response.json()["revision"] == 66
        assert PATCH_REVISION == 66
        assert app.state.pairings.items["pair_1"].name == "ubuntu03-new"
        assert app.state.pairings.saved == 1
        assert app.state.registry.summaries()[0]["device_name"] == "ubuntu03-new"


def test_worker_presentation_asset_is_bounded_and_replaces_v65_loop_owner():
    app = build_app()
    with TestClient(app) as client:
        html = client.get("/admin").text
        assert ADMIN_ASSET == "/assets/chat2api-worker-presentation-v66.js"
        assert f'<script src="{ADMIN_ASSET}"></script>' in html
        assert "chat2api-worker-presentation-v65.js" not in html
        assert "chat2api-worker-presentation-v64.js" not in html
        script = client.get(ADMIN_ASSET).text

    assert 'const VERSION = 66' in script
    assert 'th.dataset.chat2apiColumnKey = "device_name"' in script
    assert 'occupancyHeader.dataset.chat2apiColumnKey = "occupancy"' in script
    assert "请求 / 实际窗口" in script
    assert "capacity.used_units" in script
    assert "capacity.limit_units" in script
    assert 'callApi("/api/admin/window-manager")' in script
    assert "button.dataset.v66PairingRename" in script
    assert "设备名称已更新" in script

    # Critical console-liveness contract: v66 has no autonomous DOM observer or
    # repeating timer. It only runs at canonical show/reload boundaries and two
    # bounded startup passes.
    assert "MutationObserver" not in script
    assert "setInterval(" not in script
    assert script.count("setTimeout(") == 2
    assert "chat2apiReloadCanonicalWorkerListV59" in script
    assert "globalThis.show" in script


def test_worker_presentation_is_installed_after_disable_authority():
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "install_worker_presentation_v64_patch(app)" in entry
    assert entry.index("install_worker_disable_authority_patch(app)") < entry.index("install_worker_presentation_v64_patch(app)")


def test_worker_presentation_javascript_syntax():
    path = ROOT / "app" / "admin_worker_presentation_v66.js"
    result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_old_v65_asset_is_not_referenced_by_server_patch():
    source = (ROOT / "app" / "worker_presentation_v64_patch.py").read_text(encoding="utf-8")
    assert "admin_worker_presentation_v65.js" not in source
    assert "/assets/chat2api-worker-presentation-v65.js" not in source
    assert "admin_worker_presentation_v66.js" in source
    assert "autonomous MutationObservers" in source
