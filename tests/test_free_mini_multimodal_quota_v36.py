from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_free_mini_multimodal_quota_detector_is_loaded_before_account_status_overlay() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    detector = (EXTENSION / "content_multimodal_quota_v36.js").read_text(encoding="utf-8")
    background = (EXTENSION / "background_multimodal_quota_v36.js").read_text(encoding="utf-8")
    settle = (EXTENSION / "content_multimodal_settle_v85.js").read_text(encoding="utf-8")

    assert "content_multimodal_quota_v36.js" in scripts
    assert scripts.index("content_multimodal_quota_v36.js") < scripts.index("content_multimodal_v4.js")
    assert '"background_multimodal_quota_v36.js"' in entry
    assert entry.index('"background_multimodal_quota_v36.js"') < entry.index('"background_account_v20.js"')
    assert 'capabilities.add("vision")' in background
    assert 'capabilities.add("file-understanding")' in background
    assert 'capabilities.delete("vision")' in background
    assert 'capabilities.delete("file-understanding")' in background
    assert 'accountType !== "free"' in background
    assert "UNPARSED_RETRY_MS = 5 * 60 * 1000" in background
    assert "file_upload_quota_cooling" in background
    assert "file-upload-quota-aware-v91" in background
    assert "chat2api.multimodal.quota.v36" in detector
    assert "ACTIVE_MS = 120000" in detector
    assert "multimodal-upload-quota-v91" in detector
    assert "一次|单次" in detector
    assert "CHAT2API_FILE_UPLOAD_QUOTA_EXHAUSTED" in settle
    assert "upload-quota-exhausted" in settle


def test_quota_reset_parser_vm_contract() -> None:
    result = subprocess.run(
        ["node", "tests/mini_multimodal_quota_v36.mjs"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "contract passed" in result.stdout


def test_server_routes_attachment_requests_away_from_cooling_free_accounts() -> None:
    script = r'''
import tempfile
import time
from pathlib import Path
from app.config import Settings
from app.main import create_app
from app.registry import PersistedClient
from app import v13_patch
from app.v13_patch import install_v13_patch
from app.v20_patch import install_v20_patch
from app.v21_4_model_contract_patch import install_v21_4_model_contract_patch
from app.mini_multimodal_quota_patch import install_mini_multimodal_quota_patch

class DummySocket:
    async def send_json(self, payload):
        pass

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
    install_v21_4_model_contract_patch(app)
    install_mini_multimodal_quota_patch(app)
    registry = app.state.registry

    future = int(time.time() * 1000) + 2 * 60 * 60 * 1000

    def client(client_id, account_type, **metadata):
        return PersistedClient(
            client_id=client_id,
            name=client_id,
            browser_name="Chrome",
            version="0.8.1",
            token_hash="x",
            created_at="2026-08-24T10:00:00+08:00",
            metadata={"account_type": account_type, **metadata},
        )

    registry.clients["free-cooling"] = client(
        "free-cooling",
        "free",
        file_upload_available=False,
        file_upload_cooldown_until_ms=future,
        mini_multimodal_available=False,
        mini_multimodal_cooldown_until_ms=future,
    )
    registry.clients["free-ready"] = client(
        "free-ready",
        "free",
        file_upload_available=True,
        file_upload_cooldown_until_ms=0,
        mini_multimodal_available=True,
        mini_multimodal_cooldown_until_ms=0,
    )
    registry.clients["paid-fallback"] = client("paid-fallback", "paid")
    for client_id in registry.clients:
        registry.sockets[client_id] = DummySocket()

    multimodal = v13_patch._target_from_payload({
        "model": "gpt-5.5-mini",
        "messages": [{"role": "user", "content": [{"type": "input_image", "image_url": "file_demo"}]}],
    })
    assert multimodal["needs_multimodal"] is True
    token = v13_patch._target_context.set(multimodal)
    try:
        assert registry.resolve_client(None) == "free-ready"
        try:
            registry.resolve_client("free-cooling")
            raise AssertionError("explicit cooling Free client must reject attachment input")
        except LookupError as error:
            assert "file upload quota" in str(error)
    finally:
        v13_patch._target_context.reset(token)

    registry.busy_clients.add("free-ready")
    token = v13_patch._target_context.set(multimodal)
    try:
        assert registry.resolve_client(None) == "paid-fallback"
    finally:
        v13_patch._target_context.reset(token)

    # Account upload quota is not model-specific. A generic/default attachment
    # request must also skip the cooling Free Worker.
    generic_attachment = {"model": "default", "needs_multimodal": True}
    token = v13_patch._target_context.set(generic_attachment)
    try:
        assert registry.resolve_client(None) == "paid-fallback"
    finally:
        v13_patch._target_context.reset(token)

    registry.sockets.pop("paid-fallback")
    token = v13_patch._target_context.set(multimodal)
    try:
        try:
            registry.resolve_client(None)
            raise AssertionError("all cooling Free attachment clients must fail until reset")
        except ConnectionError as error:
            assert "file upload quotas are cooling down" in str(error)
    finally:
        v13_patch._target_context.reset(token)

    registry.busy_clients.clear()
    registry.sockets.pop("free-ready")
    text_only = v13_patch._target_from_payload({
        "model": "gpt-5.5-mini",
        "messages": [{"role": "user", "content": "hello"}],
    })
    assert text_only["needs_multimodal"] is False
    token = v13_patch._target_context.set(text_only)
    try:
        assert registry.resolve_client(None) == "free-cooling"
    finally:
        v13_patch._target_context.reset(token)

    catalog = {row["id"]: row for row in registry.model_catalog(online_only=True)}
    mini = catalog["gpt-5.5-mini"]
    assert mini["multimodal_available"] is False
    assert "vision" not in mini["capabilities"]
    assert "file-understanding" not in mini["capabilities"]
    assert mini["native_free_multimodal_cooling_clients"] == ["free-cooling"]
    assert mini["multimodal_resume_at"] is not None

    registry.clients["free-cooling"].metadata["file_upload_cooldown_until_ms"] = int(time.time() * 1000) - 1
    registry.clients["free-cooling"].metadata["mini_multimodal_cooldown_until_ms"] = int(time.time() * 1000) - 1
    catalog = {row["id"]: row for row in registry.model_catalog(online_only=True)}
    mini = catalog["gpt-5.5-mini"]
    assert mini["multimodal_available"] is True
    assert "vision" in mini["capabilities"]
    assert "file-understanding" in mini["capabilities"]
    assert mini["native_free_multimodal_clients"] == ["free-cooling"]
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_production_entry_installs_quota_patch_after_capacity_controls() -> None:
    entry = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .mini_multimodal_quota_patch import install_mini_multimodal_quota_patch" in entry
    assert entry.index("install_extension_capacity_control_patch(app)") < entry.index("install_mini_multimodal_quota_patch(app)")
