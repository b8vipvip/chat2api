from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.linux_worker_enable_patch as enable_patch
from app.admin_auth import SESSION_COOKIE
from app.linux_worker_enable_patch import install_linux_worker_enable_patch


ROOT = Path(__file__).resolve().parents[1]


class Sessions:
    def authenticate(self, token: str | None) -> bool:
        return token == "admin-ok"


class Workers:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.saved = 0
        self.data = {
            "workers": {
                "wrk_linux": {
                    "worker_id": "wrk_linux",
                    "name": "Linux Worker",
                    "created_at": "2026-08-27T00:00:00+08:00",
                    "revoked_at": None,
                    "status": "ready",
                    "chatgpt_status": "ready",
                    "extension_client_id": "ext_linux",
                    "metadata": {"bridge": {"client_id": "ext_linux", "socket_state": "connected"}},
                }
            }
        }

    def _save(self) -> None:
        self.saved += 1

    def public(self, worker):
        return dict(worker)

    def list_public(self):
        return [self.public(worker) for worker in self.data["workers"].values()]


class Registry:
    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.clients = {
            "ext_linux": SimpleNamespace(connection_enabled=True, metadata={}),
            "ext_windows": SimpleNamespace(connection_enabled=True, metadata={}),
        }
        self.sockets = {"ext_linux": object(), "ext_windows": object()}
        self.api_key_routes = {"key_linux": "ext_linux", "key_windows": "ext_windows"}
        self.saved = 0
        self.connection_changes: list[tuple[str, bool]] = []

    def online_client_ids(self) -> list[str]:
        return sorted(
            client_id
            for client_id in self.sockets
            if self.clients.get(client_id) and self.clients[client_id].connection_enabled
        )

    def resolve_client(self, requested: str | None) -> str:
        if requested:
            if requested not in self.online_client_ids():
                raise ConnectionError("offline")
            return requested
        ids = self.online_client_ids()
        if not ids:
            raise ConnectionError("offline")
        return ids[0]

    async def save(self) -> None:
        self.saved += 1

    async def set_connection_enabled(self, client_id: str, enabled: bool):
        item = self.clients[client_id]
        item.connection_enabled = bool(enabled)
        self.connection_changes.append((client_id, bool(enabled)))
        if not enabled:
            self.sockets.pop(client_id, None)
        return item


def make_toggle_app(monkeypatch, *, control_ok: bool = True):
    app = FastAPI()
    app.state.admin_sessions = Sessions()
    app.state.linux_workers = Workers()
    app.state.registry = Registry()
    control_calls: list[tuple[str, str, dict]] = []

    async def fake_control(runtime, client_id, action, payload, *, timeout_seconds):
        assert runtime is app
        assert app.state.registry.clients[client_id].connection_enabled is True
        assert client_id in app.state.registry.sockets
        control_calls.append((client_id, action, dict(payload or {})))
        if not control_ok:
            return {
                "ok": False,
                "action": action,
                "error": "simulated collapse failure",
                "error_code": "extension_control_failed",
                "data": {},
            }
        return {
            "ok": True,
            "control_id": "ctl_test",
            "action": action,
            "error": "",
            "error_code": "",
            "data": {
                "keep_windows": 1,
                "managed_windows_before": 3,
                "managed_windows_after": 1,
                "kept_window_ids": [101],
                "closed_window_ids": [102, 103],
                "window_snapshot": {"total": 1, "active": 0, "idle": 1},
            },
        }

    monkeypatch.setattr(enable_patch, "_request_extension_control", fake_control)
    install_linux_worker_enable_patch(app)
    return app, control_calls


def test_linux_worker_disconnect_collapses_first_then_disables_transport(monkeypatch):
    app, control_calls = make_toggle_app(monkeypatch)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    assert app.state.registry.online_client_ids() == ["ext_linux", "ext_windows"]
    assert app.state.registry.resolve_client("ext_linux") == "ext_linux"

    disabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    payload = disabled.json()
    assert control_calls == [("ext_linux", "worker.disable", {"keep_windows": 1})]
    assert payload["enabled"] is False
    assert payload["routing_state"] == "disconnected"
    assert payload["transport_preserved"] is False
    assert payload["connection_enabled"] is False
    assert payload["keep_windows"] == 1
    assert payload["closed_window_ids"] == [102, 103]
    assert payload["worker"]["enabled"] is False
    assert payload["worker"]["status"] == "offline"
    assert payload["worker"]["chatgpt_status"] == "offline"

    assert "ext_linux" in app.state.registry.clients
    assert app.state.registry.clients["ext_linux"].connection_enabled is False
    assert ("ext_linux", False) in app.state.registry.connection_changes
    assert app.state.registry.online_client_ids() == ["ext_windows"]
    assert app.state.registry.api_key_routes == {"key_windows": "ext_windows"}
    with pytest.raises(ConnectionError, match="disconnected Linux Worker"):
        app.state.registry.resolve_client("ext_linux")

    enabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    enabled_payload = enabled.json()
    assert enabled_payload["enabled"] is True
    assert enabled_payload["routing_state"] == "connected"
    assert enabled_payload["connection_enabled"] is True
    assert enabled_payload["reconnect_pending"] is True
    assert app.state.registry.clients["ext_linux"].connection_enabled is True

    # Enabling authentication does not fabricate a socket. The Extension's own
    # reconnect loop restores it, after which the normal routing list includes it.
    assert app.state.registry.online_client_ids() == ["ext_windows"]
    app.state.registry.sockets["ext_linux"] = object()
    assert app.state.registry.online_client_ids() == ["ext_linux", "ext_windows"]
    assert app.state.registry.resolve_client("ext_linux") == "ext_linux"


def test_linux_worker_disconnect_is_not_persisted_when_window_control_fails(monkeypatch):
    app, control_calls = make_toggle_app(monkeypatch, control_ok=False)
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    response = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": False})
    assert response.status_code == 409, response.text
    assert "simulated collapse failure" in response.json()["detail"]
    assert control_calls == [("ext_linux", "worker.disable", {"keep_windows": 1})]
    worker = app.state.linux_workers.data["workers"]["wrk_linux"]
    assert worker.get("enabled") is not False
    assert app.state.registry.clients["ext_linux"].connection_enabled is True
    assert "ext_linux" in app.state.registry.sockets
    assert app.state.registry.api_key_routes["key_linux"] == "ext_linux"


def test_worker_toggle_ui_reuses_legacy_button_as_master_enable_switch():
    source = (ROOT / "app" / "admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")
    for token in (
        'if (button.textContent !== nextText) button.textContent = nextText;',
        'button[data-revoke]',
        'method: "PUT"',
        '/enabled`,',
        "stopImmediatePropagation",
        'const nextText = isEnabled ? "禁用" : "启用";',
        "只保留 1 个",
        "备用 ChatGPT 窗口",
    ):
        assert token in source
    assert 'method: "DELETE"' not in source
    assert 'observer.observe(document.documentElement, { childList: true, subtree: true })' not in source
    assert 'rowsObserver.observe(workerRows, { childList: true, subtree: false })' in source


def test_request_hygiene_only_clears_automation_owned_stale_drafts():
    background = (ROOT / "chrome_extension" / "background_request_hygiene_v42.js").read_text(encoding="utf-8")
    content = (ROOT / "chrome_extension" / "content_request_hygiene_v42.js").read_text(encoding="utf-8")

    for token in (
        "chat2api.automation-tab.query",
        "routeOwnsTab",
        "warmOwnsTab",
        "reserveOwnsTab",
        "externalWarmOwnsTab",
    ):
        assert token in background

    for token in (
        "recoverManagedDraft",
        "automation_owned_tab",
        "stale_draft_recovered",
        "stale_draft_chars",
        "Could not clear stale automation draft",
        "Manual/unowned tabs retain v4's conservative unknown-draft protection",
        "priorListener(message, sender, sendResponse)",
    ):
        assert token in content


def test_persistent_draft_ownership_survives_browser_restart_and_never_stores_prompt_text():
    source = (ROOT / "chrome_extension" / "content_draft_ownership_v43.js").read_text(encoding="utf-8")
    for token in (
        'STORAGE_PREFIX = "chat2apiDraftOwnershipV43:"',
        'crypto.subtle.digest("SHA-256"',
        'status = "prepared"',
        '"draft_written"',
        "startupRecovery",
        "request-ended-before-submit",
        "startup-restored-owned-draft",
        "matchingRecord",
        "clearIfStillOwned",
        "legacyListener",
        "Non-owned text is treated as a possible human draft",
        "Receiving chat2api.request is itself authoritative automation ownership",
    ):
        assert token in source
    assert "prompt:" not in source
    assert "prompt_text" not in source


def test_generation_liveness_is_diagnostic_only_and_hard_timeout_remains():
    source = (ROOT / "chrome_extension" / "content_generation_liveness_v49.js").read_text(encoding="utf-8")
    server = (ROOT / "app" / "request_stall_patch.py").read_text(encoding="utf-8")

    for token in (
        "generation_heartbeat_sequence",
        "generation_liveness",
        "generation_control_visible",
        "track?.sawGenerating",
        "INTERVAL_MS = 20000",
    ):
        assert token in source
    assert "generation_sequence:" not in source
    assert '"generation_sequence"' in server
    assert "ABSOLUTE_REQUEST_TIMEOUT_GRACE_SECONDS" in server
    assert "_absolute_watchdog" in server


def test_bundle_load_order_and_new_scripts_parse():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.8.26"
    assert scripts.index("content_request_v5.js") < scripts.index("content_request_lifecycle_v50.js") < scripts.index("content_request_hygiene_v42.js") < scripts.index("content_draft_ownership_v43.js")
    assert scripts.index("content_draft_ownership_v43.js") < scripts.index("content_draft_managed_recovery_v55.js") < scripts.index("content_response_capture_v41.js")
    assert scripts.index("content_response_stream_recovery_v49.js") < scripts.index("content_network_stream_recovery_v55.js") < scripts.index("content_response_semantic_recovery_v51.js") < scripts.index("content_transient_retry_v50.js") < scripts.index("content_request_stall_guard_v34.js") < scripts.index("content_generation_liveness_v49.js")

    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert bootstrap.index('"content_request_v5.js"') < bootstrap.index('"content_request_hygiene_v42.js"') < bootstrap.index('"content_draft_ownership_v43.js"')
    assert '"content_draft_managed_recovery_v55.js"' in bootstrap
    assert '"content_response_stream_recovery_v49.js"' in bootstrap
    assert '"content_network_stream_recovery_v55.js"' in bootstrap
    assert '"content_generation_liveness_v49.js"' in bootstrap

    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index("conversation_dispatch.js") < entry.index("background_route_quarantine_v50.js") < entry.index("background_request_hygiene_v42.js") < entry.index("background_request_recovery_v40.js")
    assert entry.index("background_capacity_control_v35.js") < entry.index("background_worker_master_switch_v61.js")

    for filename in (
        "chrome_extension/background_request_hygiene_v42.js",
        "chrome_extension/background_route_quarantine_v50.js",
        "chrome_extension/background_worker_master_switch_v61.js",
        "chrome_extension/network_stream_main_v55.js",
        "chrome_extension/content_network_stream_recovery_v55.js",
        "chrome_extension/content_request_hygiene_v42.js",
        "chrome_extension/content_request_lifecycle_v50.js",
        "chrome_extension/content_draft_ownership_v43.js",
        "chrome_extension/content_draft_managed_recovery_v55.js",
        "chrome_extension/content_response_stream_recovery_v49.js",
        "chrome_extension/content_response_semantic_recovery_v51.js",
        "chrome_extension/content_transient_retry_v50.js",
        "chrome_extension/content_generation_liveness_v49.js",
        "app/admin_linux_worker_enable_v46.js",
        "app/admin_worker_runtime_v61.js",
        "app/admin_worker_disable_authority_v62.js",
        "app/admin_linux_worker_chinese_progress.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / filename)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{filename}\n{result.stderr}"


def test_runtime_advertises_v62_worker_authority_and_network_parser_features():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "linux_worker_enable_patch.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.56"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.26"' in runtime
    assert '"network_response_recovery": True' in runtime
    assert '"network_response_parser_v62": True' in runtime
    assert '"linux_worker_master_switch": True' in runtime
    assert '"linux_worker_disable_authority": True' in runtime
    assert '"worker_live_occupancy": True' in runtime
    assert '"worker_device_name_column": True' in runtime
    assert '"worker_pairing_rename": True' in runtime
    assert '"multimodal_upload_confirmation_v64": True' in runtime
    assert '"managed_request_draft_recovery": True' in runtime
    assert '"visible_generation_liveness": True' in runtime
    assert '"same_api_parallel_requests": True' in runtime
    assert '"failed_route_quarantine": True' in runtime
    assert '"request_controller_lifecycle_guard": True' in runtime
    assert '"chatgpt_transient_retry": True' in runtime
    assert '"single_response_observer": True' in runtime
    assert '"assistant_response_semantic_recovery": True' in runtime
    assert '"model_capability_routing_guard": True' in runtime
    assert '"worker_key_capacity_fifo_queue": True' in runtime
    assert 'RUNTIME_ASSET_PATH = "/assets/chat2api-worker-runtime-v61.js"' in patch
    assert '"worker.disable"' in patch
    assert "install_linux_worker_enable_patch(app)" in entry
    assert "install_worker_disable_authority_patch(app)" in entry
    assert "install_model_capability_routing_patch(app)" in entry
    assert entry.index("install_linux_worker_upgrade_patch(app)") < entry.index("install_linux_worker_enable_patch(app)") < entry.index("install_model_capability_routing_patch(app)")
    assert entry.rindex("install_worker_disable_authority_patch(app)") > entry.rindex("install_server_worker_sync_patch(app)")
