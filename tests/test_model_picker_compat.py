import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_manifest_loads_composer_scoped_model_controller() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.3.3"
    scripts = manifest["content_scripts"][0]["js"]
    assert "content_model_v4.js" in scripts


def test_request_router_uses_v4_controller() -> None:
    source = (EXTENSION / "model_routing.js").read_text(encoding="utf-8")
    assert "chat2api.model.prepare.v4" in source
    assert 'files: ["content_model_v4.js"]' in source


def test_picker_is_scoped_to_loaded_chatgpt_composer() -> None:
    source = (EXTENSION / "content_model_v4.js").read_text(encoding="utf-8")
    assert "form[data-type='unified-composer']" in source
    assert "pickerButtonWithinComposer" in source
    assert "waitForComposerReady" in source
    assert "READY_TIMEOUT_MS = 30000" in source
    assert "composer-pill" in source
    assert "document.querySelectorAll(selector)" in source  # menus are global only after the correct picker opens
    assert "root.querySelectorAll(selector)" in source  # picker candidates stay inside the composer


def test_picker_contains_current_chatgpt_menu_aliases() -> None:
    source = (EXTENSION / "content_model_v4.js").read_text(encoding="utf-8")
    for text in ("GPT-5.6", "极速 5.5", "智能", "中", "高", "gpt-5.3", "o3"):
        assert text.lower() in source.lower()
    assert "Visible choices:" in source
