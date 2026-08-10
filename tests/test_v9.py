import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.v7_patch import install_v7_patch
from app.v8_patch import install_v8_patch
from app.v9_patch import install_v9_patch
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


def app_v9(tmp_path: Path):
    app = create_app(settings(tmp_path))
    install_voice_patch(app)
    install_v7_patch(app)
    install_v8_patch(app)
    install_v9_patch(app)
    return app


def test_v9_removes_dictation_from_production_api_and_model_catalog(tmp_path: Path) -> None:
    with TestClient(app_v9(tmp_path)) as client:
        assert client.get("/").json()["version"] == "0.9.0"
        assert client.get("/healthz").json()["version"] == "0.9.0"
        overview = client.get("/api/admin/overview", headers=headers()).json()
        assert overview["version"] == "0.9.0"
        assert "dictation" not in overview.get("capabilities", {})
        assert "audio_transcription" not in overview.get("capabilities", {})

        models = client.get("/v1/models", headers=headers()).json()["data"]
        ids = {str(item.get("id") or "") for item in models}
        assert "gpt-live" in ids
        assert "gpt-dictation" not in ids
        assert "chatgpt-dictation" not in ids
        serialized = json.dumps(models).lower()
        assert '"dictation"' not in serialized
        assert '"audio-transcription"' not in serialized

        response = client.post(
            "/v1/audio/transcriptions",
            headers=headers(),
            json={"model": "gpt-dictation", "audio_file_id": "file_missing"},
        )
        assert response.status_code == 404


def test_v9_test_lab_layout_and_all_suite_drop_dictation() -> None:
    source = (ROOT / "app" / "admin_v9.js").read_text(encoding="utf-8")
    assert "grid-template-columns:170px 180px" in source
    assert "min-width:0!important" in source
    assert "#testFiles::file-selector-button" in source
    assert "@media(max-width:1450px)" in source
    assert "@media(max-width:900px)" in source
    assert 'filter(option => option.value === "dictation")' in source
    assert '["text", "vision", "file", "image_generation", "voice_generation", "voice_conversation"]' in source
    assert '["text", "vision", "file", "image_generation", "voice_generation", "voice_conversation", "dictation"]' not in source


def test_production_entry_installs_v9_last() -> None:
    source = (ROOT / "app" / "entry.py").read_text(encoding="utf-8")
    assert "from .v9_patch import install_v9_patch" in source
    assert source.index("install_v8_patch(app)") < source.index("install_v9_patch(app)")


def test_extension_does_not_steal_os_window_focus_and_can_create_worker_window() -> None:
    audio = (ROOT / "chrome_extension" / "audio_routing_v2.js").read_text(encoding="utf-8")
    image = (ROOT / "chrome_extension" / "image_routing.js").read_text(encoding="utf-8")
    tabs = (ROOT / "chrome_extension" / "browser_tabs.js").read_text(encoding="utf-8")
    assert "chrome.windows.update" not in audio
    assert "chrome.windows.update" not in image
    assert 'audio_window_focus_strategy: "tab-active-only"' in audio
    assert 'image_window_focus_strategy: "tab-active-only"' in image
    assert "chrome.windows.create" in tabs
    assert "focused: false" in tabs
    assert 'automationWindowStrategy: "single-tab-window"' in tabs
