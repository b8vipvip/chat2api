from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_free_account_detector_is_passive_and_loaded() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    source = (EXTENSION / "content_account_v20.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background_account_v20.js").read_text(encoding="utf-8")
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")

    assert manifest["version"] == "0.7.6"
    assert "content_account_v20.js" in scripts
    assert "client-bootstrap-free" in source
    assert "ready-composer-without-model-selector" in source
    assert "selectable-model-control" in source
    assert ".click()" not in source
    assert 'url.includes("/api/extensions/register")' in background
    assert 'account_type: "free"' in source
    assert 'id: "gpt-5.5-mini"' in background
    assert "free-account-default-model" in background
    assert entry.index('"model_routing_v2.js"') < entry.index('"background_account_v20.js"')


def test_browser_router_bypasses_free_model_ui_and_uses_paid_fallback() -> None:
    source = (EXTENSION / "model_routing_v2.js").read_text(encoding="utf-8")
    assert 'const TEXT_MODELS = ["gpt-5.6-sol", "gpt-5.5"]' in source
    assert 'const MINI_MODEL = "gpt-5.5-mini"' in source
    assert 'freeNativeMini = String(accountProfile?.account_type || "unknown")' in source
    assert 'selection_strategy: "free-account-default-mini-no-ui-selection"' in source
    assert 'effectiveModel = "gpt-5.5"' in source
    assert 'effectiveReasoning = "instant"' in source
    assert '"gpt-5.5-instant-fallback"' in source
    free_branch = source[source.index("const prepared = freeNativeMini"):source.index("const attachmentDiagnostics")]
    assert "prepareRequestedState" in free_branch
    assert "free_model_ui_bypassed: true" in free_branch


def test_server_v20_runtime_prefers_free_then_falls_back_without_poisoning_paid_routes() -> None:
    script = r'''
import asyncio
import tempfile
from pathlib import Path
from app.config import Settings
from app.main import create_app
from app.registry import PersistedClient
from app import v13_patch
from app.v13_patch import install_v13_patch
from app.v20_patch import install_v20_patch

class DummySocket:
    def __init__(self): self.payloads = []
    async def send_json(self, payload): self.payloads.append(payload)

with tempfile.TemporaryDirectory() as tmp:
    settings = Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=Path(tmp),
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )
    app = create_app(settings)
    install_v13_patch(app)
    install_v20_patch(app)
    registry = app.state.registry

    def client(client_id, account_type):
        return PersistedClient(
            client_id=client_id,
            name=client_id,
            browser_name="Chrome",
            version="0.7.5",
            token_hash="x",
            created_at="2026-08-14T10:00:00+08:00",
            metadata={"account_type": account_type},
        )

    free_socket = DummySocket()
    paid_socket = DummySocket()
    registry.clients["free-ext"] = client("free-ext", "free")
    registry.clients["paid-ext"] = client("paid-ext", "paid")
    registry.sockets["free-ext"] = free_socket
    registry.sockets["paid-ext"] = paid_socket

    assert v13_patch._normalize_model("gpt-5.5-mini") == "gpt-5.5-mini"
    target = v13_patch._target_from_payload({"model": "gpt-5.5-mini", "reasoning_effort": "high"})
    assert target["model"] == "gpt-5.5-mini"
    assert target["reasoning_effort"] is None

    token = v13_patch._target_context.set(target)
    try:
        assert registry.resolve_client(None) == "free-ext"
        asyncio.run(registry.send("free-ext", {
            "type": "chat.request", "request_id": "req-free", "options": {"model": "gpt-5.5-mini"}
        }))
    finally:
        v13_patch._target_context.reset(token)
    sent = free_socket.payloads[-1]
    assert sent["options"]["model"] == "gpt-5.5-mini"
    assert sent["options"]["mini_route"] == "free-native"
    assert sent["options"]["fallback_model"] is None

    registry.busy_clients.add("free-ext")
    token = v13_patch._target_context.set(target)
    try:
        assert registry.resolve_client(None) == "paid-ext"
        asyncio.run(registry.send("paid-ext", {
            "type": "chat.request", "request_id": "req-paid", "options": {"model": "gpt-5.5-mini"}
        }))
    finally:
        v13_patch._target_context.reset(token)
    sent = paid_socket.payloads[-1]
    assert sent["options"]["mini_route"] == "gpt-5.5-instant-fallback"
    assert sent["options"]["fallback_model"] == "gpt-5.5"
    assert sent["options"]["fallback_reasoning_effort"] == "low"

    registry.busy_clients.clear()
    paid_target = v13_patch._target_from_payload({"model": "gpt-5.5", "reasoning_effort": "low"})
    token = v13_patch._target_context.set(paid_target)
    try:
        assert registry.resolve_client(None) == "paid-ext"
        try:
            registry.resolve_client("free-ext")
            raise AssertionError("paid model must not route to a Free extension")
        except LookupError:
            pass
    finally:
        v13_patch._target_context.reset(token)

    catalog = {row["id"]: row for row in registry.model_catalog(online_only=True)}
    assert catalog["gpt-5.5-mini"]["native_free_clients"] == ["free-ext"]
    assert catalog["gpt-5.5-mini"]["fallback_clients"] == ["paid-ext"]
    summaries = {row["client_id"]: row for row in registry.summaries()}
    assert summaries["free-ext"]["account_type"] == "free"
    assert summaries["paid-ext"]["account_type"] == "paid"
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_admin_renames_bound_devices_to_extension_list_and_shows_account_type() -> None:
    source = (ROOT / "app" / "admin_v20.js").read_text(encoding="utf-8")
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    patch = (ROOT / "app" / "v20_patch.py").read_text(encoding="utf-8")

    assert 'heading.textContent = "扩展列表"' in source
    assert "账户类型" in source
    assert '>Free</span>' in source
    assert '>付费</span>' in source
    assert "native_free_clients" in patch
    assert '"fallback_reasoning_effort": "low"' in patch
    assert "from .v20_patch import install_v20_patch" in entry
    assert entry.index("install_v19_patch(app)") < entry.index("install_v20_patch(app)")
