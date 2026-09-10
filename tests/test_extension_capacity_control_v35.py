from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.extension_capacity_control_patch import install_extension_capacity_control_patch
from app.v21_13_patch import install_v21_13_patch


ROOT = Path(__file__).resolve().parents[1]


class FakeRegistry:
    def __init__(self, *, online: bool = True, legacy: bool = False) -> None:
        metadata = {} if legacy else {
            "extension_version": "0.8.30",
            "extension_control_version": 36,
            "extension_control_ready": True,
            "extension_control_transport": "authoritative-global-dispatch-v36",
        }
        self.clients = {
            "ext_test": SimpleNamespace(connection_enabled=True, version="0.8.1", metadata=metadata)
        }
        self.sockets = {"ext_test": object()} if online else {}
        self.sent: list[dict] = []

    async def send(self, client_id: str, payload: dict) -> None:
        assert client_id == "ext_test"
        self.sent.append(dict(payload))
        action = payload["action"]
        # v0.8.30 acknowledges server concurrency without resizing physical
        # browser windows. The current observer snapshot therefore stays stable.
        snapshot = {
            "total": 2,
            "active": 1,
            "idle": 1,
            "target": 0,
            "all_chatgpt_windows": 2,
            "speculative_windows": False,
            "route_window_authority": "conversation-routing-v30",
            "observed_at": "2026-09-10T15:00:00+08:00",
        }
        data = {"window_snapshot": snapshot}
        if action == "workers.resize":
            data.update({
                "target": int(payload.get("payload", {}).get("target") or 3),
                "target_reached": True,
                "pending_reason": "",
                "rounds": 0,
                "window_policy": "on-demand-single-authority-v30",
            })
        self.clients[client_id].metadata["extension_control_result"] = {
            "control_id": payload["control_id"],
            "action": action,
            "ok": True,
            "data": data,
            "error": "",
            "observed_at": snapshot["observed_at"],
        }


class RuntimeRegistry:
    async def authenticate(self, client_id: str, token: str) -> bool:
        return client_id == "ext_test" and token == "token_test"


def make_capacity_app(registry: FakeRegistry, *, limit: int = 3) -> FastAPI:
    app = FastAPI()
    app.state.registry = registry
    app.state.broker = SimpleNamespace(max_concurrency=limit)
    app.state.concurrency_config = {"max_concurrency": limit, "limit_for": lambda _client_id: limit}
    install_extension_capacity_control_patch(app)
    return app


def test_server_capacity_apply_updates_server_target_without_resizing_browser_windows() -> None:
    app = FastAPI()
    registry = FakeRegistry()
    app.state.registry = registry
    app.state.broker = SimpleNamespace(max_concurrency=3)
    app.state.concurrency_config = {"max_concurrency": 3, "limit_for": lambda client_id: 4 if client_id == "ext_test" else 3}
    install_extension_capacity_control_patch(app)
    client = TestClient(app)

    applied = client.post("/api/admin/extensions/ext_test/capacity/apply", json={"target": 4})
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["ok"] is True
    assert payload["saved"] is True
    assert payload["applied"] is True
    assert payload["target_reached"] is True
    assert payload["configured_limit"] == 4
    assert payload["window_snapshot"]["total"] == 2
    assert payload["window_snapshot"]["target"] == 0
    assert registry.sent[-1]["action"] == "workers.resize"
    assert registry.sent[-1]["payload"]["target"] == 4
    assert registry.sent[-1]["minimum_control_version"] == 36

    refreshed = client.post("/api/admin/extensions/ext_test/windows/refresh")
    assert refreshed.status_code == 200, refreshed.text
    data = refreshed.json()
    assert data["ok"] is True
    assert data["window_snapshot"]["total"] == 2
    assert data["window_snapshot"]["route_window_authority"] == "conversation-routing-v30"
    assert registry.sent[-1]["action"] == "windows.snapshot"


def test_stale_bridge_without_control_v36_fails_fast() -> None:
    registry = FakeRegistry(legacy=True)
    client = TestClient(make_capacity_app(registry, limit=5))
    started = time.perf_counter()
    refreshed = client.post("/api/admin/extensions/ext_test/windows/refresh")
    elapsed = time.perf_counter() - started
    assert refreshed.status_code == 200
    payload = refreshed.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "extension_control_not_ready"
    assert "control=v0" in payload["error"]
    assert elapsed < 2.0
    assert registry.sent == []


def test_offline_extension_returns_truthful_unconfirmed_result() -> None:
    client = TestClient(make_capacity_app(FakeRegistry(online=False), limit=3))
    applied = client.post("/api/admin/extensions/ext_test/capacity/apply", json={"target": 3})
    assert applied.status_code == 200
    assert applied.json()["saved"] is True
    assert applied.json()["applied"] is False
    assert applied.json()["error_code"] == "extension_offline"


def test_runtime_config_retires_speculative_reserve_target_but_keeps_server_concurrency() -> None:
    app = FastAPI()
    app.state.registry = RuntimeRegistry()
    app.state.broker = SimpleNamespace(max_concurrency=9)
    app.state.concurrency_config = {"max_concurrency": 9, "limit_for": lambda client_id: 5 if client_id == "ext_test" else 9}
    install_v21_13_patch(app)
    client = TestClient(app)
    response = client.get(
        "/api/extensions/runtime-config",
        headers={"X-Extension-Client-ID": "ext_test", "X-Extension-Token": "token_test"},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["reserve_window_target"] == 0
    assert payload["max_reserve_window_target"] == 0
    assert payload["worker_concurrency"] == 5
    assert payload["route_idle_close_seconds"] == 300
    assert payload["speculative_worker_windows"] is False
    assert payload["window_decision_authority"] == "conversation-routing-v30"
    assert payload["server_scheduler_authority"] == "server-single-authority-scheduler-v58"


def test_console_and_bridge_expose_server_capacity_and_observer_only_window_controls() -> None:
    concurrency = (ROOT / "app" / "admin_v21_5.js").read_text(encoding="utf-8")
    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    control = (ROOT / "chrome_extension" / "background_capacity_control_v35.js").read_text(encoding="utf-8")
    dispatcher = (ROOT / "chrome_extension" / "background_capacity_control_v36.js").read_text(encoding="utf-8")
    assert "data-worker-max" in concurrency
    assert '/capacity-v57' in concurrency
    assert '/capacity/apply' in concurrency
    assert '/windows/refresh' in concurrency
    assert '"background_capacity_control_v35.js"' in entry
    assert '"background_capacity_control_v36.js"' in entry
    assert '"background_window_observer_v90.js"' in entry
    assert 'action === "windows.snapshot"' in control
    assert 'action === "workers.resize"' in control
    assert "__CHAT2API_WINDOW_OBSERVER_V90__" in control
    assert "on-demand-single-authority-v30" in control
    assert "__CHAT2API_RESERVE_POOL_V29__" not in control
    assert "__CHAT2API_TAB_SUPERVISOR_V32__" not in control
    assert "chrome.windows.create" not in control
    assert "chrome.windows.remove" not in control
    assert "extension.control.result" in control
    assert "authoritative-global-dispatch-v36" in dispatcher


def test_capacity_control_vm_contracts() -> None:
    for filename, marker in (
        ("capacity_control_v35.mjs", "capacity_control_v35 single-authority VM contract passed"),
        ("capacity_control_v36.mjs", "capacity_control_v36 VM contract passed"),
    ):
        result = subprocess.run(["node", str(ROOT / "tests" / filename)], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker in result.stdout


def test_capacity_control_javascript_syntax() -> None:
    for filename in ("background_capacity_control_v35.js", "background_capacity_control_v36.js"):
        result = subprocess.run(["node", "--check", str(ROOT / "chrome_extension" / filename)], cwd=ROOT, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
