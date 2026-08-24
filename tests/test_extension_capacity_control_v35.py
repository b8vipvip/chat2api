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
            "extension_version": "0.8.2",
            "extension_control_version": 36,
            "extension_control_ready": True,
            "extension_control_transport": "authoritative-global-dispatch-v36",
        }
        self.clients = {
            "ext_test": SimpleNamespace(
                connection_enabled=True,
                version="0.8.1" if legacy else "0.8.2",
                metadata=metadata,
            )
        }
        self.sockets = {"ext_test": object()} if online else {}
        self.sent: list[dict] = []

    async def send(self, client_id: str, payload: dict) -> None:
        assert client_id == "ext_test"
        self.sent.append(dict(payload))
        action = payload["action"]
        target = int(payload.get("payload", {}).get("target") or 3)
        snapshot = {
            "total": target if action == "workers.resize" else 3,
            "active": 1,
            "idle": max(0, target - 1) if action == "workers.resize" else 2,
            "target": target,
            "all_chatgpt_windows": (target if action == "workers.resize" else 3) + 1,
            "observed_at": "2026-08-23T18:00:00+08:00",
        }
        data = {"window_snapshot": snapshot}
        if action == "workers.resize":
            data.update({"target_reached": True, "pending_reason": "", "rounds": 1})
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
    app.state.concurrency_config = {
        "max_concurrency": limit,
        "limit_for": lambda _client_id: limit,
    }
    install_extension_capacity_control_patch(app)
    return app


def test_server_capacity_apply_waits_for_matching_real_extension_ack() -> None:
    app = FastAPI()
    registry = FakeRegistry()
    app.state.registry = registry
    app.state.broker = SimpleNamespace(max_concurrency=3)
    app.state.concurrency_config = {
        "max_concurrency": 3,
        "limit_for": lambda client_id: 4 if client_id == "ext_test" else 3,
    }
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
    assert payload["window_snapshot"]["total"] == 4
    assert payload["window_snapshot"]["active"] == 1
    assert registry.sent[-1]["type"] == "extension.control"
    assert registry.sent[-1]["action"] == "workers.resize"
    assert registry.sent[-1]["payload"]["target"] == 4
    assert registry.sent[-1]["control_id"].startswith("ctl_")
    assert registry.sent[-1]["minimum_control_version"] == 36

    refreshed = client.post("/api/admin/extensions/ext_test/windows/refresh")
    assert refreshed.status_code == 200, refreshed.text
    data = refreshed.json()
    assert data["ok"] is True
    assert data["window_snapshot"]["total"] == 3
    assert data["window_snapshot"]["active"] == 1
    assert data["window_snapshot"]["all_chatgpt_windows"] == 4
    assert registry.sent[-1]["action"] == "windows.snapshot"


def test_stale_081_bridge_fails_fast_instead_of_15_or_70_second_timeout() -> None:
    registry = FakeRegistry(legacy=True)
    client = TestClient(make_capacity_app(registry, limit=5))

    started = time.perf_counter()
    refreshed = client.post("/api/admin/extensions/ext_test/windows/refresh")
    elapsed = time.perf_counter() - started
    assert refreshed.status_code == 200, refreshed.text
    payload = refreshed.json()
    assert payload["ok"] is False
    assert payload["error_code"] == "extension_control_not_ready"
    assert "0.8.2" in payload["error"]
    assert "0.8.1" in payload["error"]
    assert elapsed < 2.0
    assert registry.sent == []

    started = time.perf_counter()
    applied = client.post("/api/admin/extensions/ext_test/capacity/apply", json={"target": 5})
    elapsed = time.perf_counter() - started
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["saved"] is True
    assert payload["applied"] is False
    assert payload["error_code"] == "extension_control_not_ready"
    assert elapsed < 2.0
    assert registry.sent == []


def test_offline_extension_returns_truthful_unconfirmed_result() -> None:
    app = FastAPI()
    app.state.registry = FakeRegistry(online=False)
    app.state.broker = SimpleNamespace(max_concurrency=3)
    app.state.concurrency_config = {"max_concurrency": 3, "limit_for": lambda _client_id: 3}
    install_extension_capacity_control_patch(app)
    client = TestClient(app)

    applied = client.post("/api/admin/extensions/ext_test/capacity/apply", json={"target": 3})
    assert applied.status_code == 200, applied.text
    payload = applied.json()
    assert payload["saved"] is True
    assert payload["applied"] is False
    assert payload["ok"] is False
    assert payload["error_code"] == "extension_offline"
    assert "offline" in payload["error"].lower()

    refreshed = client.post("/api/admin/extensions/ext_test/windows/refresh")
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["ok"] is False
    assert "offline" in refreshed.json()["error"].lower()


def test_extension_runtime_config_uses_per_extension_limit_for_reserve_target() -> None:
    app = FastAPI()
    app.state.registry = RuntimeRegistry()
    app.state.broker = SimpleNamespace(max_concurrency=9)
    app.state.concurrency_config = {
        "max_concurrency": 9,
        "limit_for": lambda client_id: 5 if client_id == "ext_test" else 9,
    }
    install_v21_13_patch(app)
    client = TestClient(app)
    response = client.get(
        "/api/extensions/runtime-config",
        headers={
            "X-Extension-Client-ID": "ext_test",
            "X-Extension-Token": "token_test",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["reserve_window_target"] == 5


def test_console_and_bridge_expose_live_capacity_controls() -> None:
    concurrency = (ROOT / "app" / "admin_v21_5.js").read_text(encoding="utf-8")
    health = (ROOT / "app" / "admin_v21_6.js").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    background_entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    control = (ROOT / "chrome_extension" / "background_capacity_control_v35.js").read_text(encoding="utf-8")
    dispatcher = (ROOT / "chrome_extension" / "background_capacity_control_v36.js").read_text(encoding="utf-8")

    assert 'target.textContent = "并发设置"' in concurrency
    assert '/capacity/apply' in concurrency
    assert "showActionResult" in concurrency
    assert '"实时窗口"' in health
    assert "data-live-window-refresh" in health
    assert '/windows/refresh' in health
    assert "刷新成功：实时窗口" in health
    assert "install_extension_capacity_control_patch(app)" in entry
    assert entry.index("install_runtime_contract(app)") < entry.index("install_extension_capacity_control_patch(app)")
    assert '"background_capacity_control_v35.js"' in background_entry
    assert '"background_capacity_control_v36.js"' in background_entry
    assert background_entry.index('"background_capacity_control_v35.js"') < background_entry.index('"background_capacity_control_v36.js"')
    assert 'action === "windows.snapshot"' in control
    assert 'action === "workers.resize"' in control
    assert "await reserve.refreshConfig(true)" in control
    assert "await reserve.reconcile()" in control
    assert "await supervisor.reconcile()" in control
    assert "extension.control.result" in control
    assert "authoritative-global-dispatch-v36" in dispatcher
    assert "extension_control_ready" in dispatcher


def test_capacity_control_vm_contracts() -> None:
    for filename, marker in (
        ("capacity_control_v35.mjs", "capacity_control_v35 VM contract passed"),
        ("capacity_control_v36.mjs", "capacity_control_v36 VM contract passed"),
    ):
        result = subprocess.run(
            ["node", str(ROOT / "tests" / filename)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert marker in result.stdout


def test_capacity_control_javascript_syntax() -> None:
    for filename in ("background_capacity_control_v35.js", "background_capacity_control_v36.js"):
        result = subprocess.run(
            ["node", "--check", str(ROOT / "chrome_extension" / filename)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
