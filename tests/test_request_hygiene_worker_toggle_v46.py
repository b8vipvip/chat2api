from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

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
        self.clients = {"ext_linux": object(), "ext_windows": object()}
        self.api_key_routes = {"key_linux": "ext_linux", "key_windows": "ext_windows"}
        self.saved = 0

    def online_client_ids(self) -> list[str]:
        return ["ext_linux", "ext_windows"]

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


def test_linux_worker_disable_is_routing_only_and_reversible():
    app = FastAPI()
    app.state.admin_sessions = Sessions()
    app.state.linux_workers = Workers()
    app.state.registry = Registry()
    install_linux_worker_enable_patch(app)

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, "admin-ok")

    assert app.state.registry.online_client_ids() == ["ext_linux", "ext_windows"]
    assert app.state.registry.resolve_client("ext_linux") == "ext_linux"

    disabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    payload = disabled.json()
    assert payload["enabled"] is False
    assert payload["routing_state"] == "disconnected"
    assert payload["transport_preserved"] is True
    assert payload["worker"]["enabled"] is False
    assert payload["worker"]["status"] == "offline"
    assert payload["worker"]["chatgpt_status"] == "offline"

    assert "ext_linux" in app.state.registry.clients
    assert app.state.registry.online_client_ids() == ["ext_windows"]
    assert app.state.registry.api_key_routes == {"key_windows": "ext_windows"}
    with pytest.raises(ConnectionError, match="disabled Linux Worker"):
        app.state.registry.resolve_client("ext_linux")

    enabled = client.put("/api/admin/linux-workers/wrk_linux/enabled", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["enabled"] is True
    assert enabled.json()["routing_state"] == "connected"
    assert app.state.registry.online_client_ids() == ["ext_linux", "ext_windows"]
    assert app.state.registry.resolve_client("ext_linux") == "ext_linux"


def test_worker_toggle_ui_reuses_legacy_button_without_permanent_revoke():
    source = (ROOT / "app" / "admin_linux_worker_enable_v46.js").read_text(encoding="utf-8")
    for token in (
        'button.textContent = isEnabled ? "禁用" : "启用"',
        'button[data-revoke]',
        'method: "PUT"',
        '/enabled`,',
        "stopImmediatePropagation",
        "Agent/Bridge",
    ):
        assert token in source
    assert 'method: "DELETE"' not in source


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


def test_visible_generation_liveness_extends_observable_progress_without_removing_hard_timeout():
    source = (ROOT / "chrome_extension" / "content_generation_liveness_v42.js").read_text(encoding="utf-8")
    server = (ROOT / "app" / "request_stall_patch.py").read_text(encoding="utf-8")

    for token in (
        "generation_sequence",
        "generation_liveness",
        "track?.sawGenerating",
        "generationControlVisible",
        "INTERVAL_MS = 20000",
    ):
        assert token in source
    assert '"generation_sequence"' in server
    assert "ABSOLUTE_REQUEST_TIMEOUT_GRACE_SECONDS" in server
    assert "_absolute_watchdog" in server


def test_bundle_load_order_and_new_scripts_parse():
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    assert manifest["version"] == "0.8.5"
    assert scripts.index("content_request_v5.js") < scripts.index("content_request_hygiene_v42.js") < scripts.index("content_draft_ownership_v43.js")
    assert scripts.index("content_draft_ownership_v43.js") < scripts.index("content_response_capture_v41.js")
    assert scripts.index("content_request_stall_guard_v34.js") < scripts.index("content_generation_liveness_v42.js")

    bootstrap = (ROOT / "chrome_extension" / "content_bootstrap.js").read_text(encoding="utf-8")
    assert bootstrap.index('"content_request_v5.js"') < bootstrap.index('"content_request_hygiene_v42.js"') < bootstrap.index('"content_draft_ownership_v43.js"')
    assert '"content_generation_liveness_v42.js"' in bootstrap

    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert entry.index("conversation_dispatch.js") < entry.index("background_request_hygiene_v42.js") < entry.index("background_request_recovery_v40.js")

    for filename in (
        "chrome_extension/background_request_hygiene_v42.js",
        "chrome_extension/content_request_hygiene_v42.js",
        "chrome_extension/content_draft_ownership_v43.js",
        "chrome_extension/content_generation_liveness_v42.js",
        "app/admin_linux_worker_enable_v46.js",
    ):
        result = subprocess.run(
            ["node", "--check", str(ROOT / filename)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{filename}\n{result.stderr}"


def test_runtime_advertises_v46_recovery_and_toggle_features():
    runtime = (ROOT / "app" / "runtime_contract.py").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert 'SERVER_RUNTIME_VERSION = "0.22.29"' in runtime
    assert 'CHROME_BRIDGE_BUNDLE_VERSION = "0.8.5"' in runtime
    assert '"managed_request_draft_recovery": True' in runtime
    assert '"visible_generation_liveness": True' in runtime
    assert '"linux_worker_routing_toggle": True' in runtime
    assert '"server_update_recreate_guard": True' in runtime
    assert '"server_update_poll_timeout_guard": True' in runtime
    assert "install_linux_worker_enable_patch(app)" in entry
    assert entry.index("install_linux_worker_upgrade_patch(app)") < entry.index("install_linux_worker_enable_patch(app)")
