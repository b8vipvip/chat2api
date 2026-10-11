from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_auth import SESSION_COOKIE
import app.window_manager_v88_patch as window_manager_patch
from app.window_manager_v88_patch import install_window_manager_v88_patch


ROOT = Path(__file__).resolve().parents[1]


class Sessions:
    def authenticate(self, token: str | None) -> bool:
        return token == "admin-ok"


class FakeRegistry:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.sent: list[dict] = []
        self.row = {
            "client_id": "ext_windows",
            "name": "Windows Worker",
            "version": "0.22.112",
            "online": True,
            "connection_enabled": True,
            "metadata": {
                "extension_version": "0.22.112",
                "chatgpt_login_state": "ready",
                "chatgpt_login_composer_ready": True,
                "window_manager_v88": {
                    "updated_at_ms": 1000,
                    "active": [{
                        "window_no": 1,
                        "window_id": 11,
                        "tab_id": 21,
                        "status": "ready",
                        "source": "standby-observer-v152",
                        "route_key": None,
                        "opened_at_ms": 900,
                    }],
                    "closed": [],
                },
            },
        }

    def summaries(self):
        return [deepcopy(self.row)]

    async def send(self, client_id: str, payload: dict) -> None:
        self.sent.append(deepcopy(payload))
        control_id = str(payload.get("control_id") or "")
        snapshot = deepcopy(self.row["metadata"]["window_manager_v88"])
        snapshot["updated_at_ms"] += 1
        self.row["metadata"]["window_manager_v88"] = snapshot
        if self.mode in {"matching-ack", "offline-after-ack"}:
            self.row["metadata"]["window_manager_refresh_ack"] = {
                "control_id": control_id,
                "ok": True,
                "updated_at_ms": snapshot["updated_at_ms"],
                "observer_revision": 90,
                "received_at_ms": snapshot["updated_at_ms"],
            }
        elif self.mode == "wrong-ack":
            self.row["metadata"]["window_manager_refresh_ack"] = {
                "control_id": "a-different-refresh",
                "ok": True,
                "updated_at_ms": snapshot["updated_at_ms"],
                "observer_revision": 90,
            }
        if self.mode == "offline-after-ack":
            self.row["online"] = False


def make_client(monkeypatch, mode: str) -> TestClient:
    monkeypatch.setattr(window_manager_patch, "LIVE_TRUTH_TIMEOUT_SECONDS", 0.02)
    app = FastAPI()
    app.state.registry = FakeRegistry(mode)
    app.state.admin_sessions = Sessions()
    app.state.api_keys = None
    install_window_manager_v88_patch(app)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")
    return client


def test_window_truth_accepts_only_correlated_fresh_ack(monkeypatch) -> None:
    client = make_client(monkeypatch, "matching-ack")
    response = client.get("/api/admin/window-manager")
    assert response.status_code == 200, response.text
    payload = response.json()
    worker = payload["workers"][0]
    assert worker["live_verified"] is True
    assert worker["standby_window_count"] == 1
    assert payload["truth"]["standby_windows"] == 1


def test_window_truth_does_not_accept_new_timestamp_without_ack(monkeypatch) -> None:
    client = make_client(monkeypatch, "snapshot-only")
    response = client.get("/api/admin/window-manager")
    assert response.status_code == 200, response.text
    worker = response.json()["workers"][0]
    assert worker["live_verified"] is False
    assert worker["standby_window_count"] is None
    assert worker["truth_status"] == "refresh-timeout"


def test_window_truth_rejects_ack_for_another_refresh(monkeypatch) -> None:
    client = make_client(monkeypatch, "wrong-ack")
    response = client.get("/api/admin/window-manager")
    assert response.status_code == 200, response.text
    worker = response.json()["workers"][0]
    assert worker["live_verified"] is False
    assert worker["standby_window_count"] is None


def test_window_truth_is_not_live_after_worker_goes_offline(monkeypatch) -> None:
    client = make_client(monkeypatch, "offline-after-ack")
    response = client.get("/api/admin/window-manager")
    assert response.status_code == 200, response.text
    payload = response.json()
    worker = payload["workers"][0]
    assert worker["online"] is False
    assert worker["live_verified"] is False
    assert worker["standby_window_count"] is None
    assert worker["truth_status"] == "offline"
    assert payload["active"] == []


def test_active_request_and_unknown_placeholder_data_contracts() -> None:
    extension_ui = (ROOT / "app" / "admin_extension_columns.js").read_text(encoding="utf-8")
    authority = (ROOT / "app" / "worker_disable_authority_patch.py").read_text(encoding="utf-8")
    main = (ROOT / "app" / "main.py").read_text(encoding="utf-8")
    window_patch = (ROOT / "app" / "window_manager_v88_patch.py").read_text(encoding="utf-8")
    observer = (ROOT / "chrome_extension" / "background_window_observer_v90.js").read_text(encoding="utf-8")
    refresh = (ROOT / "chrome_extension" / "background_window_refresh_v129.js").read_text(encoding="utf-8")
    window_ui = (ROOT / "app" / "admin_window_manager_v88.js").read_text(encoding="utf-8")
    assert "const usedRaw = capacity.active_requests ?? row?.active_api_calls;" in extension_ui
    assert "row?.active_api_calls ?? 0" not in extension_ui
    assert '|| "0.22.102"' not in extension_ui
    assert '"运行时版本未知"' in extension_ui
    assert "def active_request_count(client_id: str) -> int | None:" in authority
    assert 'message_type == "window.manager.refresh.result"' in main
    assert "window_manager_refresh_ack" in window_patch
    assert "snapshot_updated_at >= ack_updated_at" in window_patch
    assert "live_verified = client_id in verified and online and refresh_capable" in window_patch
    assert "observer.report(true)" in refresh
    assert "const physical = await chrome.windows.getAll({ populate: true });" in observer
    assert "chrome.windows.getAll({ populate: true }).catch(() => [])" not in observer
    assert "已核验备用窗口" in window_ui


def test_linux_status_does_not_promote_missing_heartbeat_to_healthy() -> None:
    ui = (ROOT / "app" / "admin_linux_device_authority_v124.js").read_text(encoding="utf-8")
    assert 'if(!seen&&status==="ready")return ["等待心跳","warn"]' in ui
    assert 'if(["已禁用","离线","等待心跳","状态待核验"].includes(status))return "未知"' in ui
