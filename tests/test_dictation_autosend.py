import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "chrome_extension"


def test_legacy_dictation_controller_is_not_loaded_by_production_manifest() -> None:
    manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
    scripts = [name for block in manifest["content_scripts"] for name in block["js"]]
    assert not any("dictation" in name for name in scripts)
    assert "voice_main_v2.js" not in scripts
    assert "Dictation" not in manifest.get("description", "")


def test_audio_router_exposes_voice_only() -> None:
    source = (EXTENSION / "audio_routing_v2.js").read_text(encoding="utf-8")
    assert "chat2api.voice.request.v2" in source
    assert "chat2api.voice.cancel.v2" in source
    assert "dictation.request" not in source
    assert "dictation.cancel" not in source
    assert '"gpt-dictation"' not in source
    assert '"audio-transcription"' not in source
    assert '"dictation-auto-send"' not in source
    assert 'audio_window_focus_strategy: "tab-active-only"' in source
    assert "chrome.windows.update" not in source
