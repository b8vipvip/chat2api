import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
from app.v10_patch import install_v10_patch
from app.voice_patch import install_voice_patch


ROOT = Path(__file__).resolve().parents[1]


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


def app_v10(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    install_v10_patch(app)
    return app


def test_v10_version_and_console_script(tmp_path: Path) -> None:
    with TestClient(app_v10(tmp_path)) as client:
        assert client.get("/").json()["version"] == "0.10.0"
        assert client.get("/healthz").json()["version"] == "0.10.0"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.10.0"
        assert overview["capabilities"]["extension_runtime_log"] is True
        assert overview["capabilities"]["browser_local_time_display"] is True
        html = client.get("/admin").text
        assert '/assets/chat2api-v10.js' in html
        script = client.get("/assets/chat2api-v10.js")
        assert script.status_code == 200
        assert "时间（北京时间）" in script.text


def test_production_entry_installs_v10_after_v9() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v10_patch import install_v10_patch" in source
    assert source.index("install_v9_patch(app)") < source.index("install_v10_patch(app)")


def test_extension_loads_v10_request_image_voice_and_runtime_log_controllers() -> None:
    manifest = json.loads((ROOT / "chrome_extension" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.7.5"
    scripts = manifest["content_scripts"][1]["js"]
    for name in [
        "content_multimodal_v4.js",
        "content_request_v4.js",
        "content_image_v3.js",
        "content_voice_fix_v3.js",
        "content_runtime_log.js",
    ]:
        assert name in scripts

    entry = (ROOT / "chrome_extension" / "background_entry.js").read_text(encoding="utf-8")
    assert '"image_routing_v3.js"' in entry
    assert '"audio_routing_v3.js"' in entry
    assert '"background_logging.js"' in entry
    assert entry.index('"audio_routing_v3.js"') < entry.index('"background_logging.js"')


def test_request_v4_accepts_submission_evidence_instead_of_false_prompt_failure() -> None:
    source = (ROOT / "chrome_extension" / "content_request_v4.js").read_text(encoding="utf-8")
    assert "submittedEvidence(active)" in source
    assert '"prompt-auto-submitted"' in source
    assert '"confirmed-before-click"' in source
    assert "assistant_observed" in source
    assert "Prompt was inserted but ChatGPT composer did not retain it" not in source
    assert "enterFallback(message" not in source

    routing = (ROOT / "chrome_extension" / "model_routing.js").read_text(encoding="utf-8")
    assert '"content_request_v4.js"' in routing
    assert 'type:"chat2api.attach.prepare.v4"' in routing


def test_multimodal_v4_accepts_visual_preview_as_upload_confirmation() -> None:
    source = (ROOT / "chrome_extension" / "content_multimodal_v4.js").read_text(encoding="utf-8")
    assert 'reason: "visual-preview-count"' in source
    assert "now.media > before.media" in source
    assert 'attachments_controller: "multimodal-v4"' in source
    assert "automatic duplicate retry" not in source.lower()


def test_images_v3_reacquires_composer_and_requires_confirmed_submission() -> None:
    source = (ROOT / "chrome_extension" / "content_image_v3.js").read_text(encoding="utf-8")
    assert '"chat2api.image.request.v3"' in source
    assert "prompt_write_attempts" in source
    assert "submitAndConfirm(active, prompt)" in source
    assert "promptEchoedOutsideComposer" in source
    assert "baseline.get(img) !== src" in source
    assert 'submission_confirmed: true' in source

    routing = (ROOT / "chrome_extension" / "image_routing_v3.js").read_text(encoding="utf-8")
    assert 'type: "chat2api.image.request.v3"' in routing
    assert 'type: "chat2api.attach.prepare.v4"' in routing


def test_voice_v3_strict_trigger_rejects_attachment_button() -> None:
    source = (ROOT / "chrome_extension" / "content_voice_fix_v3.js").read_text(encoding="utf-8")
    assert "composer-speech-button" in source
    assert "添加文件" in source
    assert "composer-plus" in source
    assert '"chat2api.voice.trigger.prepare.v3"' in source
    assert "value += 400" in source
    assert "return -10000" in source

    routing = (ROOT / "chrome_extension" / "audio_routing_v3.js").read_text(encoding="utf-8")
    assert 'type: "chat2api.voice.trigger.prepare.v3"' in routing
    assert 'audio_router: "audio-routing-v3"' in routing


def test_extension_runtime_logs_are_downloadable_and_redacted() -> None:
    background = (ROOT / "chrome_extension" / "background_logging.js").read_text(encoding="utf-8")
    content = (ROOT / "chrome_extension" / "content_runtime_log.js").read_text(encoding="utf-8")
    popup = (ROOT / "chrome_extension" / "popup_logging.js").read_text(encoding="utf-8")
    html = (ROOT / "chrome_extension" / "popup.html").read_text(encoding="utf-8")

    assert '"popup.logs.export"' in background
    assert '"popup.logs.clear"' in background
    assert "[redacted]" in background
    assert "authorization" in background.lower()
    assert "page-state" in content
    assert "composer_chars" in content
    assert "visible_alerts" in content
    assert "composer_button_labels" in content
    assert 'id="downloadRuntimeLog"' in html
    assert 'id="clearRuntimeLog"' in html
    assert "chat2api-extension-runtime" in popup


def test_admin_v10_preserves_beijing_display_and_explains_vision_subtests() -> None:
    source = (ROOT / "app" / "admin_v10.js").read_text(encoding="utf-8")
    assert 'raw.replace(" ", "T") + "Z"' not in source
    assert "canonicalBeijingTime" in source
    assert "时间（北京时间）" in source
    assert "Asia/Shanghai" in source
    assert "图片" in source and "视频" in source
    assert "result.subtests" in source
