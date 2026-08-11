import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.v10_patch import install_v10_patch
from app.v11_patch import install_v11_patch
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


def app_v11(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    install_v11_patch(app)
    return app


def test_v11_version_capabilities_and_console_script(tmp_path: Path) -> None:
    with TestClient(app_v11(tmp_path)) as client:
        assert client.get("/").json()["version"] == "0.11.0"
        assert client.get("/healthz").json()["version"] == "0.11.0"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.11.0"
        assert overview["capabilities"]["file_test_per_attachment"] is True
        assert overview["capabilities"]["extension_auto_local_log"] is True
        assert overview["capabilities"]["voice_stale_draft_recovery"] is True
        html = client.get("/admin").text
        assert '/assets/chat2api-v11.js' in html
        script = client.get("/assets/chat2api-v11.js")
        assert script.status_code == 200
        assert "逐个文件独立测试" in script.text


def test_production_entry_installs_v11_after_v10() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v11_patch import install_v11_patch" in source
    assert source.index("install_v10_patch(app)") < source.index("install_v11_patch(app)")


def test_extension_067_enables_downloads_and_v4_voice_route() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.6.8"
    assert "downloads" in manifest["permissions"]
    scripts = manifest["content_scripts"][1]["js"]
    assert "content_voice_fix_v4.js" in scripts

    entry = (EXTENSION / "background_entry.js").read_text(encoding="utf-8")
    assert '"audio_routing_v4.js"' in entry
    assert entry.index('"audio_routing_v3.js"') < entry.index('"audio_routing_v4.js"') < entry.index('"background_logging.js"')

    bootstrap = (EXTENSION / "content_bootstrap.js").read_text(encoding="utf-8")
    assert '"content_voice_fix_v4.js"' in bootstrap


def test_runtime_log_v4_rolls_only_at_complete_jsonl_records_and_sessionizes_runs() -> None:
    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert "TARGET_BYTES = 200 * 1024" in source
    assert "RUN_IDLE_MS = 120000" in source
    assert 'ACTIVE_RUNS_KEY = "chat2apiRuntimeActiveRunsV4"' in source
    assert 'RUN_INDEX_KEY = "chat2apiRuntimeRunIndexV4"' in source
    assert 'RUN_PART_PREFIX = "chat2apiRuntimeRunPartV4:"' in source
    assert "archiveCurrentPart" in source
    assert "chrome.storage.local.set" in source
    assert "chrome.alarms.create" in source
    assert "chrome.downloads.download" not in source
    assert 'message?.routing?.api_key_id || "unrouted"' in source
    assert '"request_start"' in source and '"request_end"' in source
    assert "sessionized_by_api_key: true" in source
    threshold_at = source.index("run.current_bytes + lineBytes > TARGET_BYTES")
    archive_at = source.index("await archiveCurrentPart(run);", threshold_at)
    append_at = source.index("run.current_lines.push(line)", archive_at)
    assert threshold_at < archive_at < append_at


def test_runtime_log_tracks_only_fingerprint_for_automation_draft() -> None:
    source = (EXTENSION / "background_logging.js").read_text(encoding="utf-8")
    assert 'AUTOMATION_DRAFT_KEY = "chat2apiLastAutomationDraftV2"' in source
    assert "crypto.subtle.digest" in source
    assert "sha256: await sha256" in source
    assert "chars:" in source
    storage_block = source[source.index("[AUTOMATION_DRAFT_KEY]: {"):source.index("},\n      });", source.index("[AUTOMATION_DRAFT_KEY]: {"))]
    assert "prompt:" not in storage_block


def test_voice_v4_clears_only_matching_chat2api_stale_draft() -> None:
    source = (EXTENSION / "content_voice_fix_v4.js").read_text(encoding="utf-8")
    assert 'AUTOMATION_DRAFT_KEY = "chat2apiLastAutomationDraftV2"' in source
    assert '"chat2api.voice.trigger.prepare.v4"' in source
    assert "crypto.subtle.digest" in source
    assert "stale_automation_draft_cleared" in source
    assert "refusing to erase it" in source
    assert "composer-plus" in source
    assert "听写" in source
    assert "send|发送" in source
    assert "removeComposerAttachments" in source

    routing = (EXTENSION / "audio_routing_v4.js").read_text(encoding="utf-8")
    assert 'type: "chat2api.voice.trigger.prepare.v4"' in routing
    assert 'audio_router: "audio-routing-v4"' in routing
    assert "stale_automation_draft_cleared" in routing
    assert "stale_attachments_removed" in routing
    assert "chrome.windows.update" not in routing


def test_file_test_v11_runs_each_document_as_single_attachment_subtest() -> None:
    source = (ROOT / "app" / "admin_v11.js").read_text(encoding="utf-8")
    assert "async function documentSubtest" in source
    assert "async function fileTestV11" in source
    assert "for (const file of selected) subtests.push(await documentSubtest(file, model, testToken))" in source
    assert "[{ file_id: uploaded.id }]" in source
    assert "attachments_count || 0) !== 1" in source
    assert "default_bundle_code" in source
    assert "subtests," in source
    assert 'if (kind === "file") return fileTestV11' in source
