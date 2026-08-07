import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_manifest_loads_hybrid_model_controller() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.3.4"
    scripts = manifest["content_scripts"][0]["js"]
    assert "content_model_v5.js" in scripts


def test_request_router_uses_v5_controller_and_default_fast_path() -> None:
    source = (EXTENSION / "model_routing.js").read_text(encoding="utf-8")
    assert "chat2api.model.prepare.v5" in source
    assert "chat2api.models.discover.v5" in source
    assert 'files: ["content_model_v5.js"]' in source
    assert 'DEFAULT_MODEL_IDS = new Set(["default", "chatgpt-web", ""])' in source
    assert 'modelSelectionStrategy: "default-no-ui"' in source


def test_v5_picker_is_composer_scoped_and_waits_for_readiness() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert "form[data-type='unified-composer']" in source
    assert "composerPill" in source
    assert "waitForComposerReady" in source
    assert "READY_TIMEOUT_MS = 30000" in source
    assert "composer-pill" in source
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
    assert "思考程度" in source


def test_v5_family_menu_uses_advanced_model_structure() -> None:
    source = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    for text in ("gpt-5.6 sol", "gpt-5.5", "gpt-5.3", "o3", "高级", "模型"):
        assert text.lower() in source.lower()
    assert "Selected family could not be verified" in source


def test_api_request_default_model_is_zero_touch() -> None:
    source = (ROOT / "app" / "models.py").read_text(encoding="utf-8")
    assert 'model: str = "default"' in source
    controller = (EXTENSION / "content_model_v5.js").read_text(encoding="utf-8")
    assert 'selection_strategy: "default-no-ui"' in controller
