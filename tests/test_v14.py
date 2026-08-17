from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api_keys import ApiKeyStore
from app.config import Settings
from app.file_store import FileStore
from app.main import create_app
from app.registry import ClientRegistry
from app.telemetry import TelemetryStore
from app.test_runs import TestRunStore
from app.timezone_utils import beijing_now_iso, to_beijing_iso
from app.v10_patch import install_v10_patch
from app.v11_patch import install_v11_patch
from app.v12_patch import install_v12_patch
from app.v13_patch import install_v13_patch
from app.v14_patch import install_v14_patch
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.voice_patch import install_voice_patch


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def settings(tmp_path: Path) -> Settings:
    return Settings(
        CHAT2API_API_KEY="master-key",
        CHAT2API_PAIRING_CODE="pair-code",
        CHAT2API_PUBLIC_URL="https://chat2api.example.test",
        CHAT2API_DATA_DIR=tmp_path,
        CHAT2API_REQUEST_TIMEOUT_SECONDS=10,
        CHAT2API_DESKTOP_WAKE_TIMEOUT_SECONDS=2,
    )


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer master-key"}


def app_v14(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    install_v12_patch(app)
    install_v13_patch(app)
    install_v14_patch(app)
    return app


def test_v14_health_console_and_development_time_standard(tmp_path: Path) -> None:
    with TestClient(app_v14(tmp_path)) as client:
        health = client.get("/healthz").json()
        assert health["version"] == "0.14.2"
        assert health["timezone"] == "Asia/Shanghai"
        assert health["utc_offset"] == "+08:00"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.14.2"
        assert overview["capabilities"]["beijing_time_standard"] is True
        assert overview["capabilities"]["reasoning_family_recovery"] is True
        assert overview["capabilities"]["adaptive_reasoning_slider"] is True
        assert overview["capabilities"]["beijing_console_no_double_conversion"] is True
        assert overview["capabilities"]["automatic_reasoning_family_recovery"] is True
        html = client.get("/admin").text
        assert "/assets/chat2api-v14.js" in html
        script = client.get("/assets/chat2api-v14.js")
        assert script.status_code == 200
        assert "Asia/Shanghai" in script.text
        assert "v0.14.2" in script.text
        assert "智能/自动" in script.text

    docs = (ROOT / "docs" / "DEVELOPMENT.md").read_text(encoding="utf-8")
    assert "Asia/Shanghai" in docs
    assert "Date.now()" in docs
    assert "performance.now()" in docs
    assert "被动 DOM" in docs
    assert "不得假定只有三个键盘步进" in docs
    assert "北京时间" in docs
    assert "智能/自动" in docs


def test_beijing_time_helpers_convert_legacy_utc() -> None:
    assert beijing_now_iso().endswith("+08:00")
    assert to_beijing_iso("2026-08-11T09:28:27.792Z") == "2026-08-11T17:28:27.792+08:00"
    assert to_beijing_iso("2026-08-11T17:28:27.792+08:00") == "2026-08-11T17:28:27.792+08:00"


def test_persistent_stores_write_and_normalize_beijing_times(tmp_path: Path) -> None:
    async def run() -> None:
        telemetry = TelemetryStore(tmp_path)
        await telemetry.append({"request_id": "req1", "recorded_at": "2026-08-11T09:28:27.792Z"})
        assert telemetry.recent(1)[0]["recorded_at"].endswith("+08:00")

        tests = TestRunStore(tmp_path)
        row = await tests.append({
            "run_id": "run1",
            "started_at": "2026-08-11T09:28:20.899Z",
            "finished_at": "2026-08-11T09:28:34.964Z",
        })
        assert row["started_at"] == "2026-08-11T17:28:20.899+08:00"
        assert row["finished_at"] == "2026-08-11T17:28:34.964+08:00"
        assert row["recorded_at"].endswith("+08:00")

        keys = ApiKeyStore(tmp_path, "master-key")
        await keys.load()
        item, _token = await keys.create("A")
        assert item["created_at"].endswith("+08:00")

        files = FileStore(tmp_path)
        stored = await files.create(
            filename="a.txt",
            data_base64="aGVsbG8=",
            mime_type="text/plain",
            owner_key_id="master",
        )
        assert stored.created_at.endswith("+08:00")

        registry = ClientRegistry(tmp_path)
        await registry.load()
        client_id, _token = await registry.register("Chrome", "Chrome", "0.7.4", {})
        assert registry.clients[client_id].created_at.endswith("+08:00")

    asyncio.run(run())


def test_extension_runtime_log_uses_beijing_time_as_canonical() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    background = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    popup = (EXTENSION / "popup_logging.js").read_text(encoding="utf-8")
    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    time_overlay = (EXTENSION / "background_time_v14.js").read_text(encoding="utf-8")

    assert 'timezone: "Asia/Shanghai"' in background
    assert 'timestamp_timezone: "Asia/Shanghai"' in background
    assert '+08:00' in background
    assert 'canonical_timezone: "Asia/Shanghai"' in popup
    assert "canonical UTC" not in popup
    assert '"background_time_v14.js"' in entry
    assert "chat2apiBeijingIso" in time_overlay
    assert "Date.now() + RUN_IDLE_MS" in background


def test_extension_recovers_family_transitions_and_uses_adaptive_reasoning() -> None:
    router = (EXTENSION / "model_routing_v2.js").read_text(encoding="utf-8")
    detector = (EXTENSION / "content_model_v7.js").read_text(encoding="utf-8")
    transition = (EXTENSION / "content_model_transition_v15.js").read_text(encoding="utf-8")
    reasoning = (EXTENSION / "content_reasoning_v7.js").read_text(encoding="utf-8")
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = manifest["content_scripts"][1]["js"]
    bootstrap = (EXTENSION / "content_bootstrap.js").read_text(encoding="utf-8")

    assert "waitForPassiveFamily" in router
    assert "family_verification_recovered" in router
    assert "postFamily" in router
    assert "prepareReasoning(tab.id, reasoning)" in router
    assert router.index("prepareRequestedState(tab, requestedModel, requestedReasoning)") < router.index("chrome.tabs.sendMessage(tab.id")
    assert "combined composer pill" in detector
    assert "family-transition-inference-v15" in transition
    assert 'auto: ["智能", "自动", "auto", "automatic"]' in transition
    assert "智能/自动" in transition
    assert 'message?.type !== "chat2api.model.prepare.v5"' in transition
    assert "content_model_transition_v15.js" in scripts
    assert '"content_model_transition_v15.js"' in bootstrap

    assert "MAX_SLIDER_STEPS = 32" in reasoning
    assert 'key(slider, "Home", "Home")' in reasoning
    assert 'key(slider, "End", "End")' in reasoning
    assert 'key(slider, "ArrowRight", "ArrowRight")' in reasoning
    assert "slider-keyboard-adaptive" in reasoning
    assert 'controller: "reasoning-v7.2"' in reasoning


def test_admin_console_does_not_double_convert_beijing_time() -> None:
    source = (ROOT / "app" / "admin_v10.js").read_text(encoding="utf-8")
    assert 'raw.replace(" ", "T") + "Z"' not in source
    assert "canonicalBeijingTime" in source
    assert "时间（北京时间）" in source
    assert "Asia/Shanghai" in source


def test_current_patch_overrides_legacy_request_diagnostic_version() -> None:
    source = (ROOT / "app" / "v14_patch.py").read_text(encoding="utf-8")
    assert 'path.startswith("/api/admin/requests/") and path.endswith("/log")' in source
    assert 'payload["server_version"] = PATCH_VERSION' in source


def test_extension_popup_shows_manifest_version() -> None:
    html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
    script = (EXTENSION / "popup.js").read_text(encoding="utf-8")
    assert 'id="versionInfo"' in html
    assert "chrome.runtime.getManifest().version" in script
    assert "Chrome Bridge · v" in script


def test_production_entry_installs_v14_after_v13() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v14_patch import install_v14_patch" in source
    assert source.index("install_v13_patch(app)") < source.index("install_v14_patch(app)")
