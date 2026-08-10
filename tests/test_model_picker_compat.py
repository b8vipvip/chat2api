import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_manifest_loads_hybrid_and_multimodal_controllers() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.5.0"
    scripts = [name for block in manifest["content_scripts"] for name in block["js"]]
    assert "voice_main.js" in scripts
    assert "content_model_v5.js" in scripts
    assert "content_model_v6.js" in scripts
    assert "content_multimodal.js" in scripts
    assert "content_guard.js" in scripts
    assert "content_image.js" in scripts
    assert "content_voice.js" in scripts


def test_request_router_uses_state_probe_before_v5_selection_and_attachments() -> None:
    source = (EXTENSION / "model_routing.js").read_text(encoding="utf-8")
    assert "chat2api.model.probe.v6" in source
    assert "chat2api.model.commit.v6" in source
    assert "chat2api.model.prepare.v5" in source
    assert 'DEFAULT_MODEL_IDS = new Set(["default", "chatgpt-web", ""])' in source
    assert "state-match-zero-op" in source
    assert 'type:"chat.diagnostics"' in source
    assert "chat2api.attach.prepare" in source


def test_image_router_targets_chatgpt_images() -> None:
    source = (EXTENSION / "image_routing.js").read_text(encoding="utf-8")
    assert 'IMAGES_URL = "https://chatgpt.com/images/"' in source
    assert 'message.type!=="image.request"' in source
    assert "chat2api.image.request" in source
    assert "chat2api.attach.prepare" in source


def test_v6_state_cache_invalidates_on_manual_composer_control() -> None:
    source = (EXTENSION / "content_model_v6.js").read_text(encoding="utf-8")
    assert "sessionStorage" in source
    assert "manual-composer-control" in source
    assert "cache_trusted" in source
    assert "zero_op" in source
    assert "state_detect_ms" in source


def test_v5_picker_is_composer_scoped_and_waits_for_readiness() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert "form[data-type='unified-composer']" in source
    assert "composerPill" in source
    assert "waitForComposerReady" in source
    assert "READY_TIMEOUT_MS = 30000" in source
    assert "rejectedButton" in source


def test_v5_reasoning_uses_shortcut_slider_and_text_fallbacks() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert 'code: "KeyM"' in source
    assert "ctrlKey: true" in source
    assert "shiftKey: true" in source
    assert "REASONING_POSITIONS" in source
    assert "visibleSlider" in source
    assert "setSliderPosition" in source
    assert "思考强度" in source


def test_api_request_default_model_is_zero_touch() -> None:
    source = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    assert 'model: str = "default"' in source
    router = (EXTENSION / "model_routing.js").read_text(encoding="utf-8")
    assert "default-no-ui" in router
